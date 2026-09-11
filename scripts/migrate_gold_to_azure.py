"""One-off data-only migration of validated Gold tables to Azure MySQL.

This utility is not the normal ETL pipeline. It copies already validated Gold
rows exactly and must not be used to rerun transformation logic.
"""

import argparse
import os
import sys
from typing import Any

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is listed in requirements.txt
    load_dotenv = None


BATCH_SIZE = 500
DATABASE_NAME = "asg_airlines"
GOLD_TABLES = (
    "dim_flights",
    "dim_passengers",
    "booking_payment_summary",
    "fact_bookings",
)
REFERENCE_TABLES = (
    "airline_prefix_reference",
    "airport_reference",
    "payment_method_reference",
    "pipeline_config",
)
RAW_CLEAN_EXPECTED_COUNTS = {
    "raw_flights": 1020,
    "raw_bookings": 1012,
    "raw_passengers": 1039,
    "raw_payments": 1000,
    "clean_flights": 964,
    "clean_bookings": 1000,
    "clean_passengers": 1000,
    "clean_payments": 1000,
}
GOLD_EXPECTED_COUNTS = {
    "dim_flights": 964,
    "dim_passengers": 1000,
    "booking_payment_summary": 1000,
    "fact_bookings": 1000,
}


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


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def _identity(connection: Any) -> dict[str, str]:
    row = connection.execute(text(
        "SELECT @@hostname AS hostname, DATABASE() AS database_name, "
        "CURRENT_USER() AS current_user_name"
    )).mappings().one()
    return {
        "hostname": str(row["hostname"]),
        "database": str(row["database_name"]),
        "user": str(row["current_user_name"]),
    }


def _verify_azure_identity(connection: Any, azure_host: str) -> None:
    if connection.execute(text("SELECT DATABASE()")).scalar_one() != DATABASE_NAME:
        raise RuntimeError("Target connection is not using the asg_airlines database.")
    suffix = ".mysql.database.azure.com"
    if not azure_host.endswith(suffix):
        raise RuntimeError("AZURE_DB_HOST is not an Azure MySQL server FQDN.")
    expected_server_name = azure_host[:-len(suffix)]
    row = connection.execute(
        text("SHOW GLOBAL VARIABLES LIKE 'azure_server_name'")
    ).mappings().one_or_none()
    actual_server_name = None if row is None else row.get("Value")
    if actual_server_name != expected_server_name:
        raise RuntimeError(
            "Azure server identity does not match AZURE_DB_HOST; refusing to migrate."
        )


def _table_names(connection: Any) -> set[str]:
    rows = connection.execute(text("""
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = :database_name
          AND TABLE_TYPE = 'BASE TABLE'
    """), {"database_name": DATABASE_NAME})
    return {str(row[0]) for row in rows}


def _columns(connection: Any, table: str) -> list[str]:
    rows = connection.execute(text("""
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = :database_name
          AND TABLE_NAME = :table_name
        ORDER BY ORDINAL_POSITION
    """), {"database_name": DATABASE_NAME, "table_name": table})
    return [str(row[0]) for row in rows]


def _count(connection: Any, table: str) -> int:
    return int(connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one())


def _count_query(connection: Any, statement: str) -> int:
    return int(connection.execute(text(statement)).scalar_one())


def _verify_reference_tables_empty(connection: Any, label: str) -> None:
    occupied = {
        table: _count(connection, table)
        for table in REFERENCE_TABLES
        if _count(connection, table) != 0
    }
    if occupied:
        raise RuntimeError(f"{label} reference tables are not empty: {occupied}")


def _verify_gold_integrity(connection: Any, label: str) -> None:
    checks = {
        "duplicate_flight_keys": _count_query(connection, """
            SELECT COUNT(*) FROM (
                SELECT flight_id FROM dim_flights GROUP BY flight_id HAVING COUNT(*) > 1
            ) duplicates
        """),
        "duplicate_passenger_keys": _count_query(connection, """
            SELECT COUNT(*) FROM (
                SELECT passenger_id FROM dim_passengers GROUP BY passenger_id HAVING COUNT(*) > 1
            ) duplicates
        """),
        "duplicate_summary_booking_keys": _count_query(connection, """
            SELECT COUNT(*) FROM (
                SELECT booking_id FROM booking_payment_summary
                GROUP BY booking_id HAVING COUNT(*) > 1
            ) duplicates
        """),
        "duplicate_fact_booking_keys": _count_query(connection, """
            SELECT COUNT(*) FROM (
                SELECT booking_id FROM fact_bookings GROUP BY booking_id HAVING COUNT(*) > 1
            ) duplicates
        """),
        "multiple_payment_booking_groups": _count_query(connection, """
            SELECT COUNT(*) FROM (
                SELECT booking_id FROM clean_payments
                GROUP BY booking_id HAVING COUNT(*) > 1
            ) multiple_payments
        """),
        "multiple_payment_gold_groups": _count_query(connection, """
            SELECT COUNT(*) FROM booking_payment_summary
            WHERE payment_category = 'MULTIPLE_PAYMENTS'
        """),
        "unmatched_flight_facts": _count_query(connection, """
            SELECT COUNT(*) FROM fact_bookings
            WHERE flight_match_status = 'UNMATCHED_FLIGHT'
        """),
        "clean_payment_total": str(
            connection.execute(text("SELECT COALESCE(SUM(amount), 0) FROM clean_payments")).scalar_one()
        ),
        "gold_payment_total": str(
            connection.execute(text("SELECT COALESCE(SUM(total_payment_amount), 0) FROM booking_payment_summary")).scalar_one()
        ),
    }
    if any(checks[key] != 0 for key in (
        "duplicate_flight_keys", "duplicate_passenger_keys",
        "duplicate_summary_booking_keys", "duplicate_fact_booking_keys",
    )):
        raise RuntimeError(f"{label} Gold duplicate-key validation failed: {checks}")
    if checks["multiple_payment_booking_groups"] != 267 or checks["multiple_payment_gold_groups"] != 267:
        raise RuntimeError(f"{label} multiple-payment validation failed: {checks}")
    if checks["clean_payment_total"] != "7982087.99" or checks["gold_payment_total"] != "7982087.99":
        raise RuntimeError(f"{label} payment-total validation failed: {checks}")
    if checks["unmatched_flight_facts"] != 42:
        raise RuntimeError(f"{label} unmatched-flight validation failed: {checks}")


def _verify_raw_clean_counts(connection: Any, label: str) -> None:
    actual = {table: _count(connection, table) for table in RAW_CLEAN_EXPECTED_COUNTS}
    if actual != RAW_CLEAN_EXPECTED_COUNTS:
        raise RuntimeError(f"{label} RAW/CLEAN counts differ from validated counts: {actual}")


def _insert_statement(table: str, columns: list[str]) -> Any:
    column_sql = ", ".join(f"`{column}`" for column in columns)
    value_sql = ", ".join(f":column_{index}" for index in range(len(columns)))
    return text(f"INSERT INTO `{table}` ({column_sql}) VALUES ({value_sql})")


def _copy_table(source: Engine, target: Engine, table: str, source_count: int) -> int:
    with source.connect() as source_connection:
        columns = _columns(source_connection, table)
        select_statement = text(
            f"SELECT {', '.join(f'`{column}`' for column in columns)} FROM `{table}`"
        )
        insert_statement = _insert_statement(table, columns)
        copied = 0
        with target.begin() as target_connection:
            result = source_connection.execution_options(stream_results=True).execute(select_statement)
            while True:
                batch = result.mappings().fetchmany(BATCH_SIZE)
                if not batch:
                    break
                parameters = [
                    {f"column_{index}": row[column] for index, column in enumerate(columns)}
                    for row in batch
                ]
                target_connection.execute(insert_statement, parameters)
                copied += len(parameters)
        if copied != source_count:
            raise RuntimeError(f"{table} copied {copied} rows; source count was {source_count}.")

    with target.connect() as target_connection:
        target_count = _count(target_connection, table)
    if target_count != source_count:
        raise RuntimeError(f"{table} target count {target_count} != source count {source_count}.")
    return target_count


def migrate(preflight_only: bool = False) -> None:
    if load_dotenv is not None:
        config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", ".env"))
        load_dotenv(config_path, override=False)

    azure_host = _required_environment("AZURE_DB_HOST")
    azure_port = int(_required_environment("AZURE_DB_PORT"))
    azure_user = _required_environment("AZURE_DB_USER")
    azure_password = _required_environment("AZURE_DB_PASSWORD")
    azure_database = _required_environment("AZURE_DB_NAME")
    if azure_database != DATABASE_NAME:
        raise RuntimeError(f"AZURE_DB_NAME must be exactly {DATABASE_NAME}.")

    source_password = os.getenv("DB_PASSWORD")
    if source_password is None:
        raise RuntimeError("DB_PASSWORD is required for the local source connection.")

    source = _make_engine("127.0.0.1", 3306, "root", source_password, DATABASE_NAME)
    target = _make_engine(azure_host, azure_port, azure_user, azure_password, azure_database)
    try:
        with source.connect() as source_connection, target.connect() as target_connection:
            source_identity = _identity(source_connection)
            target_identity = _identity(target_connection)
            if source_identity["database"] != DATABASE_NAME:
                raise RuntimeError("Source connection is not using the asg_airlines database.")
            _verify_azure_identity(target_connection, azure_host)
            if target_identity["database"] != DATABASE_NAME:
                raise RuntimeError("Target connection is not using the asg_airlines database.")
            if source_identity["hostname"] == target_identity["hostname"]:
                raise RuntimeError("Source and target appear to be the same server; refusing to migrate.")

            source_tables = _table_names(source_connection)
            target_tables = _table_names(target_connection)
            for label, tables, available in (
                ("Source", GOLD_TABLES, source_tables),
                ("Target", GOLD_TABLES, target_tables),
            ):
                missing = sorted(set(tables) - available)
                if missing:
                    raise RuntimeError(f"{label} is missing Gold tables: {', '.join(missing)}")
            for table in GOLD_TABLES:
                if _columns(source_connection, table) != _columns(target_connection, table):
                    raise RuntimeError(f"Column mismatch for Gold table: {table}")

            target_counts = {table: _count(target_connection, table) for table in GOLD_TABLES}
            occupied = {table: count for table, count in target_counts.items() if count != 0}
            if occupied:
                raise RuntimeError(f"Target Gold tables are not empty: {occupied}")

            source_counts = {table: _count(source_connection, table) for table in GOLD_TABLES}
            if source_counts != GOLD_EXPECTED_COUNTS:
                raise RuntimeError(f"Source Gold counts differ from validated counts: {source_counts}")
            _verify_raw_clean_counts(source_connection, "Source")
            _verify_raw_clean_counts(target_connection, "Target")
            _verify_reference_tables_empty(source_connection, "Source")
            _verify_reference_tables_empty(target_connection, "Target")
            _verify_gold_integrity(source_connection, "Source")

            print("Source identity:", source_identity["hostname"], source_identity["database"], source_identity["user"])
            print("Target identity:", target_identity["hostname"], target_identity["database"], target_identity["user"])
            if preflight_only:
                print("PRE-FLIGHT PASSED: Gold data is safe to migrate. No data was modified.")
                return
            print("Pre-migration checks passed. Starting Gold data-only copy.")

        summary = []
        for table in GOLD_TABLES:
            try:
                target_count = _copy_table(source, target, table, source_counts[table])
            except Exception as error:
                print(f"Migration stopped at table {table}: {error}", file=sys.stderr)
                raise
            summary.append((table, source_counts[table], target_count, "PASS"))
            print(f"{table}: source={source_counts[table]} target={target_count} status=PASS")

        with source.connect() as source_connection, target.connect() as target_connection:
            for table in GOLD_TABLES:
                source_count = _count(source_connection, table)
                target_count = _count(target_connection, table)
                if source_count != target_count:
                    raise RuntimeError(f"Final reconciliation failed for {table}.")
            _verify_raw_clean_counts(source_connection, "Source")
            _verify_raw_clean_counts(target_connection, "Target")
            _verify_reference_tables_empty(source_connection, "Source")
            _verify_reference_tables_empty(target_connection, "Target")
            _verify_gold_integrity(source_connection, "Source")
            _verify_gold_integrity(target_connection, "Target")
            print("Final Gold source-vs-target reconciliation: PASS")
            print("Gold integrity validation: PASS")
            print("table | source_count | target_count | status")
            for table, source_count, target_count, status in summary:
                print(f"{table} | {source_count} | {target_count} | {status}")
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate validated Gold tables to Azure MySQL.")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run read-only Gold safety checks without migrating data.",
    )
    args = parser.parse_args()
    migrate(preflight_only=args.preflight_only)
