"""Safely synchronize validated local ASG Airlines data to populated Azure MySQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


DATABASE_NAME = "asg_airlines"
BATCH_SIZE = 500


@dataclass(frozen=True)
class SyncTable:
    name: str
    conflict_columns: tuple[str, ...]
    immutable_columns: tuple[str, ...]


SYNC_TABLES = (
    SyncTable("etl_runs", ("run_id",), ("run_id",)),
    SyncTable("etl_batches", ("batch_id",), ("batch_id",)),
    SyncTable("raw_flights", ("raw_flight_id",), ("raw_flight_id",)),
    SyncTable("raw_bookings", ("raw_booking_id",), ("raw_booking_id",)),
    SyncTable("raw_passengers", ("raw_passenger_id",), ("raw_passenger_id",)),
    SyncTable("raw_payments", ("raw_payment_row_id",), ("raw_payment_row_id",)),
    SyncTable("clean_flights", ("flight_id",), ("clean_flight_id", "flight_id")),
    SyncTable("clean_bookings", ("booking_id",), ("clean_booking_row_id", "booking_id")),
    SyncTable("clean_passengers", ("passenger_id",), ("clean_passenger_row_id", "passenger_id")),
    SyncTable("clean_payments", ("payment_id",), ("payment_id",)),
    SyncTable("audit_log", ("audit_id",), ("audit_id",)),
    SyncTable("quarantine_records", ("quarantine_id",), ("quarantine_id",)),
    SyncTable("dim_flights", ("flight_id",), ("flight_id",)),
    SyncTable("dim_passengers", ("passenger_id",), ("passenger_id",)),
    SyncTable("booking_payment_summary", ("booking_id",), ("booking_id",)),
    SyncTable("fact_bookings", ("booking_id",), ("booking_id",)),
)
CORE_TABLES = SYNC_TABLES[:12]
GOLD_TABLES = SYNC_TABLES[12:]
EVENT_KEY_COLUMNS = {
    "audit_log": (
        "run_id", "batch_id", "entity_name", "record_identifier",
        "rule_identifier", "action", "before_value", "after_value",
        "reason", "confidence",
    ),
    "quarantine_records": (
        "run_id", "batch_id", "entity_name", "source_row_num",
        "record_identifier", "issue_category", "reason", "severity",
        "original_payload", "trace_details",
    ),
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return f"`{value}`"


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def _load_configuration() -> None:
    if load_dotenv is not None:
        load_dotenv(Path(__file__).resolve().parents[1] / "config" / ".env", override=False)


def _make_engine(host: str, port: int, user: str, password: str, database: str) -> Engine:
    return create_engine(
        URL.create(
            drivername="mysql+pymysql",
            username=user,
            password=password,
            host=host,
            port=port,
            database=database,
        ),
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def _local_engine() -> Engine:
    return _make_engine(
        os.getenv("DB_HOST", "127.0.0.1"),
        int(os.getenv("DB_PORT", "3306")),
        os.getenv("DB_USER", "root"),
        os.getenv("DB_PASSWORD", ""),
        os.getenv("DB_NAME", DATABASE_NAME),
    )


def _azure_engine() -> Engine:
    database = _required_environment("AZURE_DB_NAME")
    if database != DATABASE_NAME:
        raise RuntimeError(f"AZURE_DB_NAME must be exactly {DATABASE_NAME}.")
    return _make_engine(
        _required_environment("AZURE_DB_HOST"),
        int(_required_environment("AZURE_DB_PORT")),
        _required_environment("AZURE_DB_USER"),
        _required_environment("AZURE_DB_PASSWORD"),
        database,
    )


def _verify_target_identity(connection: Any, azure_host: str) -> None:
    identity = connection.execute(text(
        "SELECT @@hostname AS hostname, DATABASE() AS database_name"
    )).mappings().one()
    if identity["database_name"] != DATABASE_NAME:
        raise RuntimeError("Azure target is not using the asg_airlines database.")
    suffix = ".mysql.database.azure.com"
    if not azure_host.endswith(suffix):
        raise RuntimeError("AZURE_DB_HOST is not an Azure MySQL server FQDN.")
    expected_server = azure_host[:-len(suffix)]
    server_row = connection.execute(
        text("SHOW GLOBAL VARIABLES LIKE 'azure_server_name'")
    ).mappings().one_or_none()
    actual_server = None if server_row is None else server_row.get("Value")
    if actual_server != expected_server:
        raise RuntimeError("Azure server identity does not match AZURE_DB_HOST; refusing to synchronize.")


def _columns(connection: Any, table: str) -> list[str]:
    rows = connection.execute(text("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
        ORDER BY ORDINAL_POSITION
    """), {"table_name": table})
    return [str(row[0]) for row in rows]


def _column_metadata(connection: Any, table: str) -> list[dict[str, Any]]:
    rows = connection.execute(text("""
        SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT,
               ORDINAL_POSITION, COLUMN_KEY, COLLATION_NAME,
               CHARACTER_SET_NAME, EXTRA
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
        ORDER BY ORDINAL_POSITION
    """), {"table_name": table}).mappings()
    return [dict(row) for row in rows]


def _index_metadata(connection: Any, table: str) -> list[tuple[Any, ...]]:
    rows = connection.execute(text("""
        SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME,
               COLLATION, SUB_PART, INDEX_TYPE
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name
        ORDER BY INDEX_NAME, SEQ_IN_INDEX
    """), {"table_name": table}).mappings()
    return [
        (
            row["INDEX_NAME"], row["NON_UNIQUE"], row["SEQ_IN_INDEX"],
            row["COLUMN_NAME"], row["COLLATION"], row["SUB_PART"], row["INDEX_TYPE"],
        )
        for row in rows
    ]


def _table_count(connection: Any, table: str) -> int:
    return int(connection.execute(text(f"SELECT COUNT(*) FROM {_identifier(table)}")).scalar_one())


def build_upsert_sql(table: SyncTable, columns: Iterable[str]) -> str:
    column_list = list(columns)
    if not column_list:
        raise ValueError(f"No columns supplied for {table.name}")
    quoted_columns = ", ".join(_identifier(column) for column in column_list)
    binds = ", ".join(f":column_{index}" for index in range(len(column_list)))
    updates = [
        f"{_identifier(column)} = VALUES({_identifier(column)})"
        for column in column_list
        if column not in table.immutable_columns
    ]
    if not updates:
        updates = [f"{_identifier(column)} = { _identifier(column) }" for column in table.conflict_columns]
    return (
        f"INSERT INTO {_identifier(table.name)} ({quoted_columns}) VALUES ({binds}) "
        f"ON DUPLICATE KEY UPDATE {', '.join(updates)}"
    )


def logical_event_key(table: str, row: dict[str, Any]) -> str:
    key_columns = EVENT_KEY_COLUMNS[table]
    value = {column: row.get(column) for column in key_columns}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def event_sync_plan(
    table: str,
    existing_rows: Iterable[dict[str, Any]],
    source_rows: Iterable[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    existing_by_key = {logical_event_key(table, row): row for row in existing_rows}
    updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    inserts: list[dict[str, Any]] = []
    for row in source_rows:
        current = existing_by_key.get(logical_event_key(table, row))
        if current is None:
            inserts.append(row)
        else:
            updates.append((current, row))
    return updates, inserts


def _validate_table(connection: Any, table: SyncTable) -> list[str]:
    columns = _columns(connection, table.name)
    if not columns:
        raise RuntimeError(f"Required table is missing: {table.name}")
    missing = sorted(set(table.conflict_columns) - set(columns))
    if missing:
        raise RuntimeError(f"{table.name} is missing conflict columns: {', '.join(missing)}")
    return columns


def schema_difference(local_columns: Iterable[str], azure_columns: Iterable[str]) -> dict[str, list[str]]:
    local = list(local_columns)
    azure = list(azure_columns)
    return {
        "missing_in_local": [column for column in azure if column not in local],
        "missing_in_azure": [column for column in local if column not in azure],
    }


def schema_metadata_difference(
    local_metadata: list[dict[str, Any]],
    azure_metadata: list[dict[str, Any]],
    local_indexes: list[tuple[Any, ...]],
    azure_indexes: list[tuple[Any, ...]],
) -> dict[str, Any]:
    local_by_name = {row["COLUMN_NAME"]: row for row in local_metadata}
    azure_by_name = {row["COLUMN_NAME"]: row for row in azure_metadata}
    differences: dict[str, Any] = {}
    common_names = sorted(set(local_by_name) & set(azure_by_name))
    fields = (
        "COLUMN_TYPE", "IS_NULLABLE", "COLUMN_DEFAULT", "COLUMN_KEY",
        "COLLATION_NAME", "CHARACTER_SET_NAME", "EXTRA",
    )
    column_differences = {}
    for name in common_names:
        mismatch = {
            field: (local_by_name[name].get(field), azure_by_name[name].get(field))
            for field in fields
            if local_by_name[name].get(field) != azure_by_name[name].get(field)
        }
        if mismatch:
            column_differences[name] = mismatch
    if column_differences:
        differences["column_differences"] = column_differences
    if sorted(local_indexes) != sorted(azure_indexes):
        differences["index_differences"] = {
            "local_only": [item for item in local_indexes if item not in azure_indexes],
            "azure_only": [item for item in azure_indexes if item not in local_indexes],
        }
    return differences


def _verify_schemas(source: Engine, target: Engine, tables: tuple[SyncTable, ...]) -> dict[str, list[str]]:
    schemas: dict[str, list[str]] = {}
    with source.connect() as source_connection, target.connect() as target_connection:
        for table in tables:
            source_columns = _validate_table(source_connection, table)
            target_columns = _validate_table(target_connection, table)
            name_difference = schema_difference(source_columns, target_columns)
            metadata_difference = schema_metadata_difference(
                _column_metadata(source_connection, table.name),
                _column_metadata(target_connection, table.name),
                _index_metadata(source_connection, table.name),
                _index_metadata(target_connection, table.name),
            )
            if name_difference["missing_in_local"] or name_difference["missing_in_azure"] or metadata_difference:
                raise RuntimeError(
                    f"Column mismatch for {table.name}; refusing to synchronize. "
                    f"missing_in_local={name_difference['missing_in_local']} "
                    f"missing_in_azure={name_difference['missing_in_azure']} "
                    f"metadata_difference={metadata_difference}"
                )
            schemas[table.name] = source_columns
    return schemas


def _sync_table(source: Engine, target: Engine, table: SyncTable, columns: list[str], preflight_only: bool) -> dict[str, int]:
    source_count = 0
    with source.connect() as source_connection:
        select_sql = text(
            f"SELECT {', '.join(_identifier(column) for column in columns)} "
            f"FROM {_identifier(table.name)}"
        )
        result = source_connection.execution_options(stream_results=True).execute(select_sql)
        if preflight_only:
            while result.fetchmany(BATCH_SIZE):
                pass
            return {"source_count": _table_count(source_connection, table.name), "inserted_or_updated": 0, "target_count": -1}

        if table.name in EVENT_KEY_COLUMNS:
            return _sync_event_table(source_connection, target, table, columns, result)

        upsert = text(build_upsert_sql(table, columns))
        with target.begin() as target_connection:
            while True:
                batch = result.mappings().fetchmany(BATCH_SIZE)
                if not batch:
                    break
                parameters = [
                    {f"column_{index}": row[column] for index, column in enumerate(columns)}
                    for row in batch
                ]
                target_connection.execute(upsert, parameters)
                source_count += len(parameters)
            target_count = _table_count(target_connection, table.name)
    if target_count < source_count:
        raise RuntimeError(f"{table.name} target count {target_count} is below local count {source_count}.")
    return {"source_count": source_count, "inserted_or_updated": source_count, "target_count": target_count}


def _sync_event_table(
    source_connection: Any,
    target: Engine,
    table: SyncTable,
    columns: list[str],
    source_result: Any,
) -> dict[str, int]:
    identity_column = table.immutable_columns[0]
    with target.begin() as target_connection:
        target_rows = target_connection.execute(text(
            f"SELECT {', '.join(_identifier(column) for column in columns)} "
            f"FROM {_identifier(table.name)}"
        )).mappings()
        existing_rows = []
        while True:
            batch = target_rows.fetchmany(BATCH_SIZE)
            if not batch:
                break
            existing_rows.extend(dict(row) for row in batch)

        source_rows = []
        while True:
            batch = source_result.mappings().fetchmany(BATCH_SIZE)
            if not batch:
                break
            source_rows.extend(dict(row) for row in batch)
        updates, inserts = event_sync_plan(table.name, existing_rows, source_rows)

        update_columns = [
            column for column in columns
            if column != identity_column and column not in EVENT_KEY_COLUMNS[table.name]
        ]
        if update_columns:
            update_sql = text(
                f"UPDATE {_identifier(table.name)} SET "
                f"{', '.join(f'{_identifier(column)} = :{column}' for column in update_columns)} "
                f"WHERE {_identifier(identity_column)} = :sync_identity"
            )
            for current, source_row in updates:
                parameters = {column: source_row.get(column) for column in update_columns}
                parameters["sync_identity"] = current[identity_column]
                target_connection.execute(update_sql, parameters)

        insert_columns = [column for column in columns if column != identity_column]
        insert_sql = text(
            f"INSERT INTO {_identifier(table.name)} ({', '.join(_identifier(column) for column in insert_columns)}) "
            f"VALUES ({', '.join(f':{column}' for column in insert_columns)})"
        )
        for start in range(0, len(inserts), BATCH_SIZE):
            batch = inserts[start:start + BATCH_SIZE]
            target_connection.execute(
                insert_sql,
                [{column: row.get(column) for column in insert_columns} for row in batch],
            )
        target_count = _table_count(target_connection, table.name)

    return {
        "source_count": len(source_rows),
        "inserted_or_updated": len(updates) + len(inserts),
        "target_count": target_count,
    }


def reconcile_counts(source: dict[str, int], target: dict[str, int]) -> None:
    mismatches = {table: (source[table], target[table]) for table in source if source[table] != target.get(table)}
    if mismatches:
        raise RuntimeError(f"Local/Azure count reconciliation failed: {mismatches}")


def _key_check(connection: Any, table: SyncTable) -> int:
    columns = ", ".join(_identifier(column) for column in table.conflict_columns)
    return int(connection.execute(text(
        f"SELECT COUNT(*) FROM (SELECT {columns} FROM {_identifier(table.name)} "
        f"GROUP BY {columns} HAVING COUNT(*) > 1) duplicate_keys"
    )).scalar_one())


def synchronize(preflight_only: bool = False, scope: str = "all") -> dict[str, Any]:
    _load_configuration()
    source = _local_engine()
    target = _azure_engine()
    try:
        azure_host = _required_environment("AZURE_DB_HOST")
        with source.connect() as source_connection, target.connect() as target_connection:
            source_identity = source_connection.execute(text(
                "SELECT @@hostname AS hostname, DATABASE() AS database_name"
            )).mappings().one()
            if source_identity["database_name"] != DATABASE_NAME:
                raise RuntimeError("Local source is not using the asg_airlines database.")
            _verify_target_identity(target_connection, azure_host)
            target_identity = target_connection.execute(text("SELECT @@hostname AS hostname")).mappings().one()
            if source_identity["hostname"] == target_identity["hostname"]:
                raise RuntimeError("Local source and Azure target appear to be the same host; refusing to synchronize.")
        tables = {
            "all": SYNC_TABLES,
            "core": CORE_TABLES,
            "gold": GOLD_TABLES,
        }[scope]
        schemas = _verify_schemas(source, target, tables)
        summaries: dict[str, dict[str, int]] = {}
        source_counts: dict[str, int] = {}
        target_counts: dict[str, int] = {}
        for table in tables:
            summary = _sync_table(source, target, table, schemas[table.name], preflight_only)
            summaries[table.name] = summary
            source_counts[table.name] = summary["source_count"]
            if not preflight_only:
                target_counts[table.name] = summary["target_count"]
        if not preflight_only:
            reconcile_counts(source_counts, target_counts)
            with source.connect() as source_connection, target.connect() as target_connection:
                for table in tables:
                    if _key_check(source_connection, table) or _key_check(target_connection, table):
                        raise RuntimeError(f"Duplicate synchronization keys detected in {table.name}.")
        return {"preflight_only": preflight_only, "tables": summaries}
    finally:
        source.dispose()
        target.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize validated local data to populated Azure MySQL.")
    parser.add_argument("--preflight-only", action="store_true", help="Validate schemas and read local rows without modifying Azure.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--core-only", action="store_true", help="Synchronize control, RAW, CLEAN, audit, and quarantine tables.")
    scope.add_argument("--gold-only", action="store_true", help="Synchronize the four Gold tables.")
    args = parser.parse_args()
    try:
        selected_scope = "core" if args.core_only else "gold" if args.gold_only else "all"
        result = synchronize(preflight_only=args.preflight_only, scope=selected_scope)
        print("AZURE SYNC PRE-FLIGHT PASSED" if args.preflight_only else "AZURE INCREMENTAL SYNC PASSED")
        for table, summary in result["tables"].items():
            print(f"{table}: local={summary['source_count']} target={summary['target_count']}")
        return 0
    except Exception as error:
        print(f"Azure synchronization failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
