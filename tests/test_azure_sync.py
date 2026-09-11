from __future__ import annotations

from pathlib import Path

import pytest

import scripts.sync_local_to_azure as sync
from scripts.run_pipeline import redact


class ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class SourceResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.read = False

    def mappings(self) -> "SourceResult":
        return self

    def fetchmany(self, size: int) -> list[dict[str, object]]:
        if self.read:
            return []
        self.read = True
        return self.rows


class SourceConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.result = SourceResult(rows)

    def execute(self, statement, parameters=None):
        if "COUNT(*)" in str(statement):
            return ScalarResult(len(self.result.rows))
        return self.result

    def execution_options(self, **kwargs):
        return self


class ConnectionContext:
    def __init__(self, connection) -> None:
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *args):
        return False


class TargetConnection:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.executed = []

    def execute(self, statement, parameters=None):
        if self.fail:
            raise RuntimeError("target write failed")
        self.executed.append((str(statement), parameters))
        if "COUNT(*)" in str(statement):
            return ScalarResult(1)
        return ScalarResult(1)


class TargetEngine:
    def __init__(self, connection: TargetConnection) -> None:
        self.connection = connection
        self.rolled_back = False

    def begin(self):
        engine = self

        class Transaction:
            def __enter__(self):
                return engine.connection

            def __exit__(self, exc_type, exc_value, traceback):
                if exc_type:
                    engine.rolled_back = True
                return False

        return Transaction()

    def connect(self):
        return ConnectionContext(self.connection)


def test_key_based_upsert_prevents_duplicates_and_updates_changed_values() -> None:
    table = sync.SyncTable("clean_flights", ("flight_id",), ("clean_flight_id", "flight_id"))
    sql = sync.build_upsert_sql(table, ["clean_flight_id", "flight_id", "airline", "duration_minutes"])
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "`airline` = VALUES(`airline`)" in sql
    assert "`duration_minutes` = VALUES(`duration_minutes`)" in sql
    assert "`clean_flight_id` = VALUES(`clean_flight_id`)" not in sql
    assert "DELETE" not in sql.upper()


def test_audit_log_same_logical_event_is_idempotent() -> None:
    row = {
        "audit_id": 101,
        "run_id": "run-1",
        "batch_id": "batch-1",
        "entity_name": "flights",
        "record_identifier": "F1",
        "rule_identifier": "RULE",
        "action": "impute",
        "before_value": None,
        "after_value": "Air India",
        "reason": "derived",
        "confidence": 0.95,
        "event_timestamp": "2026-01-01 00:00:00",
    }
    updates, inserts = sync.event_sync_plan("audit_log", [row], [dict(row, audit_id=999, event_timestamp="2026-01-02 00:00:00")])
    assert len(updates) == 1
    assert inserts == []


def test_quarantine_log_same_event_updates_but_new_event_inserts() -> None:
    existing = {
        "quarantine_id": 10,
        "run_id": "run-1",
        "batch_id": "batch-1",
        "entity_name": "flights",
        "source_row_num": 2,
        "record_identifier": "F1",
        "issue_category": "missing_airline",
        "reason": "missing",
        "severity": "error",
        "original_payload": "{}",
        "trace_details": "source_file=xlsx",
        "status": "quarantined",
        "created_at": "2026-01-01",
        "reviewed_at": None,
        "review_decision": None,
    }
    changed = dict(existing, quarantine_id=999, status="reviewed", reviewed_at="2026-01-02")
    different = dict(existing, quarantine_id=1000, source_row_num=3, record_identifier="F2")
    updates, inserts = sync.event_sync_plan("quarantine_records", [existing], [changed, different])
    assert len(updates) == 1
    assert len(inserts) == 1


def test_batch_size_is_bounded() -> None:
    assert sync.BATCH_SIZE == 500
    assert sync.BATCH_SIZE > 0


def test_sync_table_rolls_back_target_transaction_on_failure() -> None:
    source = type("SourceEngine", (), {"connect": lambda self: ConnectionContext(SourceConnection([{"id": 1}]))})()
    target_connection = TargetConnection(fail=True)
    target = TargetEngine(target_connection)
    table = sync.SyncTable("sample", ("id",), ("id",))
    with pytest.raises(RuntimeError, match="target write failed"):
        sync._sync_table(source, target, table, ["id"], preflight_only=False)
    assert target.rolled_back


def test_count_reconciliation_detects_mismatch() -> None:
    with pytest.raises(RuntimeError, match="count reconciliation"):
        sync.reconcile_counts({"dim_flights": 2}, {"dim_flights": 1})


def test_all_gold_schema_column_lists_match_when_authoritative() -> None:
    gold_columns = {
        "dim_flights": ["flight_id", "airline", "source", "destination", "departure_time", "arrival_time", "duration_minutes", "flight_date", "route"],
        "dim_passengers": ["passenger_id", "age", "age_group", "gender", "date_of_birth"],
        "booking_payment_summary": ["booking_id", "payment_count", "total_payment_amount", "payment_method_summary", "payment_category"],
        "fact_bookings": ["booking_id", "passenger_id", "flight_id", "airline", "source", "destination", "booking_date", "booking_year", "booking_month", "booking_month_name", "booking_day", "status", "seat_number", "departure_time", "arrival_time", "duration_minutes", "flight_date", "route", "passenger_age_group", "payment_count", "total_payment_amount", "payment_method_summary", "payment_category", "flight_match_status"],
    }
    for columns in gold_columns.values():
        assert sync.schema_difference(columns, columns) == {"missing_in_local": [], "missing_in_azure": []}


def test_dim_flights_legacy_columns_are_detected_as_azure_mismatch() -> None:
    authoritative = ["flight_id", "airline", "source", "destination", "departure_time", "arrival_time", "duration_minutes", "flight_date", "route"]
    local = authoritative + ["duration_anomaly_flag", "duration_anomaly_reason"]
    assert sync.schema_difference(local, authoritative) == {
        "missing_in_local": [],
        "missing_in_azure": ["duration_anomaly_flag", "duration_anomaly_reason"],
    }


def test_dim_flights_column_order_difference_is_compatible() -> None:
    local = [
        {"COLUMN_NAME": "flight_id", "COLUMN_TYPE": "varchar(64)", "IS_NULLABLE": "NO", "COLUMN_DEFAULT": None, "COLUMN_KEY": "PRI", "COLLATION_NAME": "utf8mb4_0900_ai_ci", "CHARACTER_SET_NAME": "utf8mb4", "EXTRA": "", "ORDINAL_POSITION": 1},
        {"COLUMN_NAME": "flight_date", "COLUMN_TYPE": "date", "IS_NULLABLE": "YES", "COLUMN_DEFAULT": None, "COLUMN_KEY": "MUL", "COLLATION_NAME": None, "CHARACTER_SET_NAME": None, "EXTRA": "", "ORDINAL_POSITION": 2},
    ]
    azure = [dict(local[0]), dict(local[1], ORDINAL_POSITION=2)]
    azure[1], azure[0] = azure[0], azure[1]
    assert sync.schema_metadata_difference(local, azure, [], []) == {}


def test_schema_mismatch_refuses_synchronization(monkeypatch) -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Engine:
        def __init__(self):
            self.connection = Connection()

        def connect(self):
            return self.connection

    source = Engine()
    target = Engine()
    table = sync.SyncTable("dim_flights", ("flight_id",), ("flight_id",))
    authoritative = ["flight_id", "airline"]
    local = authoritative + ["duration_anomaly_flag"]

    def fake_columns(connection, table_name):
        return local if connection is source.connection else authoritative

    monkeypatch.setattr(sync, "_columns", fake_columns)
    monkeypatch.setattr(sync, "_column_metadata", lambda connection, table_name: [
        {"COLUMN_NAME": column, "COLUMN_TYPE": "varchar(64)", "IS_NULLABLE": "NO", "COLUMN_DEFAULT": None, "COLUMN_KEY": "PRI" if column == "flight_id" else "", "COLLATION_NAME": "utf8mb4_0900_ai_ci", "CHARACTER_SET_NAME": "utf8mb4", "EXTRA": "", "ORDINAL_POSITION": index}
        for index, column in enumerate(local if connection is source.connection else authoritative, start=1)
    ])
    monkeypatch.setattr(sync, "_index_metadata", lambda connection, table_name: [])
    with pytest.raises(RuntimeError, match=r"missing_in_azure=\['duration_anomaly_flag'\]"):
        sync._verify_schemas(source, target, (table,))


def test_secret_redaction_is_shared_with_orchestrator() -> None:
    import os

    old = os.environ.get("AZURE_DB_PASSWORD")
    os.environ["AZURE_DB_PASSWORD"] = "test-secret-value"
    try:
        assert "test-secret-value" not in redact("failure test-secret-value")
    finally:
        if old is None:
            os.environ.pop("AZURE_DB_PASSWORD", None)
        else:
            os.environ["AZURE_DB_PASSWORD"] = old


def test_sync_loader_uses_repository_config_without_exposing_values(monkeypatch) -> None:
    loaded = []
    monkeypatch.setattr(sync, "load_dotenv", lambda path, override=False: loaded.append((Path(path), override)))
    sync._load_configuration()
    assert loaded
    assert loaded[0][0].as_posix().endswith("config/.env")
    assert loaded[0][1] is False
