"""One-off data-only migration from validated local MySQL to Azure MySQL.

This script is not the normal ETL pipeline. It copies only the explicit
allowlisted tables and must not be used for ingestion, cleaning, validation,
or transformation reruns.
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
TABLES = (
    "etl_runs",
    "etl_batches",
    "raw_flights",
    "raw_bookings",
    "raw_passengers",
    "raw_payments",
    "clean_flights",
    "clean_bookings",
    "clean_passengers",
    "clean_payments",
    "audit_log",
    "quarantine_records",
)
EXPECTED_COUNTS = {
    "etl_runs": 1,
    "etl_batches": 23,
    "raw_flights": 1020,
    "raw_bookings": 1012,
    "raw_passengers": 1039,
    "raw_payments": 1000,
    "clean_flights": 964,
    "clean_bookings": 1000,
    "clean_passengers": 1000,
    "clean_payments": 1000,
    "audit_log": 78,
    "quarantine_records": 107,
}


def _make_engine(host: str, port: int, user: str, password: str, database: str) -> Engine:
    url = URL.create(
        drivername="mysql+pymysql",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800)


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def _identity(connection: Any) -> dict[str, str]:
    row = connection.execute(
        text("SELECT @@hostname AS hostname, DATABASE() AS database_name, CURRENT_USER() AS current_user_name")
    ).mappings().one()
    return {
        "hostname": str(row["hostname"]),
        "database": str(row["database_name"]),
        "user": str(row["current_user_name"]),
    }


def _table_names(connection: Any) -> set[str]:
    rows = connection.execute(
        text("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = :database_name
              AND TABLE_TYPE = 'BASE TABLE'
        """),
        {"database_name": DATABASE_NAME},
    )
    return {str(row[0]) for row in rows}


def _columns(connection: Any, table: str) -> list[str]:
    rows = connection.execute(
        text("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :database_name
              AND TABLE_NAME = :table_name
            ORDER BY ORDINAL_POSITION
        """),
        {"database_name": DATABASE_NAME, "table_name": table},
    )
    return [str(row[0]) for row in rows]


def _verify_schema_columns(source_connection: Any, target_connection: Any) -> None:
    for table in TABLES:
        source_columns = _columns(source_connection, table)
        target_columns = _columns(target_connection, table)
        if not source_columns:
            raise RuntimeError(f"Source table does not exist or has no columns: {table}")
        if source_columns != target_columns:
            raise RuntimeError(
                f"Column mismatch for {table}; source and target schemas must match before migration."
            )


def _count(connection: Any, table: str) -> int:
    return int(connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one())


def _insert_statement(table: str, columns: list[str]) -> Any:
    quoted_columns = ", ".join(f"`{column}`" for column in columns)
    bind_names = ", ".join(f":column_{index}" for index in range(len(columns)))
    return text(f"INSERT INTO `{table}` ({quoted_columns}) VALUES ({bind_names})")


def _copy_table(source: Engine, target: Engine, table: str, source_count: int) -> int:
    with source.connect() as source_connection:
        source_columns = _columns(source_connection, table)
        statement = text(
            f"SELECT {', '.join(f'`{column}`' for column in source_columns)} "
            f"FROM `{table}`"
        )
        insert = _insert_statement(table, source_columns)
        copied = 0
        with target.begin() as target_connection:
            result = source_connection.execution_options(stream_results=True).execute(statement)
            while True:
                batch = result.mappings().fetchmany(BATCH_SIZE)
                if not batch:
                    break
                parameters = [
                    {f"column_{index}": row[column] for index, column in enumerate(source_columns)}
                    for row in batch
                ]
                target_connection.execute(insert, parameters)
                copied += len(parameters)

        if copied != source_count:
            raise RuntimeError(
                f"Table {table} copied {copied} rows but source snapshot was {source_count}."
            )

    with target.connect() as target_connection:
        target_count = _count(target_connection, table)
    if target_count != source_count:
        raise RuntimeError(
            f"Table {table} target count {target_count} does not match source count {source_count}."
        )
    return target_count


def _verify_payment_preservation(connection: Any, label: str) -> None:
    duplicate_payment_ids = _count_query(connection, """
        SELECT COUNT(*) FROM (
            SELECT payment_id
            FROM clean_payments
            GROUP BY payment_id
            HAVING COUNT(*) > 1
        ) duplicate_groups
    """)
    multiple_payment_bookings = _count_query(connection, """
        SELECT COUNT(*) FROM (
            SELECT booking_id
            FROM clean_payments
            GROUP BY booking_id
            HAVING COUNT(*) > 1
        ) multiple_payment_bookings
    """)
    if duplicate_payment_ids != 0:
        raise RuntimeError(f"{label} has {duplicate_payment_ids} duplicate payment_id groups.")
    if multiple_payment_bookings != 267:
        raise RuntimeError(
            f"{label} has {multiple_payment_bookings} multiple-payment booking groups; expected 267."
        )


def _count_query(connection: Any, statement: str) -> int:
    return int(connection.execute(text(statement)).scalar_one())


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
            if target_identity["database"] != DATABASE_NAME:
                raise RuntimeError("Target connection is not using the asg_airlines database.")
            azure_suffix = ".mysql.database.azure.com"
            if not azure_host.endswith(azure_suffix):
                raise RuntimeError("AZURE_DB_HOST is not an Azure MySQL server FQDN.")
            expected_azure_server_name = azure_host[:-len(azure_suffix)]
            azure_server_row = target_connection.execute(
                text("SHOW GLOBAL VARIABLES LIKE 'azure_server_name'")
            ).mappings().one_or_none()
            actual_azure_server_name = None if azure_server_row is None else azure_server_row.get("Value")
            if actual_azure_server_name != expected_azure_server_name:
                raise RuntimeError(
                    "Azure server identity does not match AZURE_DB_HOST; refusing to migrate."
                )
            if source_identity["hostname"] == target_identity["hostname"]:
                raise RuntimeError("Source and target appear to be the same server; refusing to migrate.")

            target_tables = _table_names(target_connection)
            missing_tables = sorted(set(TABLES) - target_tables)
            if missing_tables:
                raise RuntimeError(f"Target is missing required tables: {', '.join(missing_tables)}")

            source_tables = _table_names(source_connection)
            missing_source_tables = sorted(set(TABLES) - source_tables)
            if missing_source_tables:
                raise RuntimeError(f"Source is missing required tables: {', '.join(missing_source_tables)}")
            _verify_schema_columns(source_connection, target_connection)

            target_counts = {table: _count(target_connection, table) for table in TABLES}
            occupied = {table: count for table, count in target_counts.items() if count != 0}
            if occupied:
                raise RuntimeError(f"Target tables are not empty: {occupied}")

            source_counts = {table: _count(source_connection, table) for table in TABLES}
            for table, expected in EXPECTED_COUNTS.items():
                if source_counts[table] != expected:
                    raise RuntimeError(
                        f"Source snapshot for {table} is {source_counts[table]}, expected {expected}."
                    )

            print("Source identity:", source_identity["hostname"], source_identity["database"], source_identity["user"])
            print("Target identity:", target_identity["hostname"], target_identity["database"], target_identity["user"])
            if preflight_only:
                print("PRE-FLIGHT PASSED: No data was modified. Safe to proceed with migration.")
                return
            print("Pre-migration checks passed. Starting data-only copy.")

        summary = []
        for table in TABLES:
            try:
                target_count = _copy_table(source, target, table, source_counts[table])
            except Exception as error:
                print(f"Migration stopped at table {table}: {error}", file=sys.stderr)
                raise
            summary.append((table, source_counts[table], target_count, "PASS"))
            print(f"{table}: source={source_counts[table]} target={target_count} status=PASS")

        with source.connect() as source_connection, target.connect() as target_connection:
            _verify_payment_preservation(source_connection, "Source")
            _verify_payment_preservation(target_connection, "Target")
            for table in TABLES:
                source_count = _count(source_connection, table)
                target_count = _count(target_connection, table)
                if source_count != target_count:
                    raise RuntimeError(
                        f"Final reconciliation failed for {table}: source={source_count}, target={target_count}."
                    )
            print("Final source-vs-target reconciliation: PASS")
            print("Payment preservation: PASS")
            print("Migration summary:")
            print("table | source_count | target_count | status")
            for table, source_count, target_count, status in summary:
                print(f"{table} | {source_count} | {target_count} | {status}")
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate validated local MySQL data to Azure MySQL.")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run read-only safety checks without migrating data.",
    )
    args = parser.parse_args()
    migrate(preflight_only=args.preflight_only)
