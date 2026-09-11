"""Build reporting-ready tables from the existing CLEAN layer."""

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from .db import get_engine


REPORTING_TABLES = (
	"fact_bookings",
	"booking_payment_summary",
	"dim_flights",
	"dim_passengers",
)

DURATION_ANOMALY_COLUMNS = {
	"duration_anomaly_flag": "TINYINT(1) NOT NULL DEFAULT 0",
	"duration_anomaly_reason": "VARCHAR(64) NULL",
}


def _execute_schema(connection: Any) -> None:
	schema_path = Path(__file__).resolve().parent.parent / "sql" / "06_reporting_tables.sql"
	schema_sql = "\n".join(
		line for line in schema_path.read_text(encoding="utf-8").splitlines()
		if not line.lstrip().startswith("--")
	)
	for statement in schema_sql.split(";"):
		statement = statement.strip()
		if statement and not statement.upper().startswith("USE "):
			connection.execute(text(statement))


def _ensure_duration_anomaly_columns(connection: Any) -> None:
	existing = {
		row[0]
		for row in connection.execute(text("""
			SELECT COLUMN_NAME
			FROM INFORMATION_SCHEMA.COLUMNS
			WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dim_flights'
		"""))
	}
	for column, definition in DURATION_ANOMALY_COLUMNS.items():
		if column not in existing:
			connection.execute(text(f"ALTER TABLE dim_flights ADD COLUMN {column} {definition}"))


def _verify_reporting_tables(connection: Any) -> dict[str, Any]:
	checks = {
		"duplicate_flight_keys": int(connection.execute(text("""
			SELECT COUNT(*) FROM (
				SELECT flight_id FROM dim_flights GROUP BY flight_id HAVING COUNT(*) > 1
			) duplicates
		""")).scalar_one()),
		"duplicate_passenger_keys": int(connection.execute(text("""
			SELECT COUNT(*) FROM (
				SELECT passenger_id FROM dim_passengers GROUP BY passenger_id HAVING COUNT(*) > 1
			) duplicates
		""")).scalar_one()),
		"duplicate_booking_payment_keys": int(connection.execute(text("""
			SELECT COUNT(*) FROM (
				SELECT booking_id FROM booking_payment_summary GROUP BY booking_id HAVING COUNT(*) > 1
			) duplicates
		""")).scalar_one()),
		"duplicate_fact_booking_keys": int(connection.execute(text("""
			SELECT COUNT(*) FROM (
				SELECT booking_id FROM fact_bookings GROUP BY booking_id HAVING COUNT(*) > 1
			) duplicates
		""")).scalar_one()),
		"multi_payment_booking_groups": int(connection.execute(text("""
			SELECT COUNT(*) FROM (
				SELECT booking_id FROM clean_payments GROUP BY booking_id HAVING COUNT(*) > 1
			) multiple_payments
		""")).scalar_one()),
		"multi_payment_reporting_groups": int(connection.execute(text("""
			SELECT COUNT(*) FROM booking_payment_summary
			WHERE payment_category = 'MULTIPLE_PAYMENTS'
		""")).scalar_one()),
		"unmatched_flight_facts": int(connection.execute(text("""
			SELECT COUNT(*) FROM fact_bookings
			WHERE flight_match_status = 'UNMATCHED_FLIGHT'
		""")).scalar_one()),
		"clean_payment_total": str(connection.execute(text("SELECT COALESCE(SUM(amount), 0) FROM clean_payments")).scalar_one()),
		"reporting_payment_total": str(connection.execute(text("SELECT COALESCE(SUM(total_payment_amount), 0) FROM booking_payment_summary")).scalar_one()),
	}
	anomaly_stats = connection.execute(text("""
		SELECT COUNT(*) AS total_clean_flights,
		       COUNT(duration_minutes) AS valid_duration_count,
		       AVG(duration_minutes) AS mean_duration,
		       STDDEV_POP(duration_minutes) AS standard_deviation,
		       AVG(duration_minutes) - 2 * STDDEV_POP(duration_minutes) AS lower_threshold,
		       AVG(duration_minutes) + 2 * STDDEV_POP(duration_minutes) AS upper_threshold,
		       COALESCE(SUM(duration_anomaly_flag), 0) AS anomaly_count
		FROM dim_flights
	""")).mappings().one()
	checks.update({key: str(value) if value is not None else None for key, value in anomaly_stats.items()})
	checks["anomaly_percentage"] = str(
		connection.execute(text("""
			SELECT COALESCE(100 * SUM(duration_anomaly_flag) / NULLIF(COUNT(*), 0), 0)
			FROM dim_flights
		""")).scalar_one()
	)
	checks["duplicate_anomaly_flight_ids"] = int(connection.execute(text("""
		SELECT COUNT(*) FROM (
			SELECT flight_id FROM dim_flights
			WHERE duration_anomaly_flag = 1
			GROUP BY flight_id HAVING COUNT(*) > 1
		) duplicates
	""")).scalar_one())
	checks["null_anomaly_flight_ids"] = int(connection.execute(text("""
		SELECT COUNT(*) FROM dim_flights
		WHERE duration_anomaly_flag = 1 AND flight_id IS NULL
	""")).scalar_one())
	if checks["duplicate_anomaly_flight_ids"] or checks["null_anomaly_flight_ids"]:
		raise RuntimeError(f"Duration anomaly key validation failed: {checks}")
	if checks["duplicate_flight_keys"] or checks["duplicate_passenger_keys"] or checks["duplicate_booking_payment_keys"] or checks["duplicate_fact_booking_keys"]:
		raise RuntimeError(f"Reporting key validation failed: {checks}")
	if checks["multi_payment_booking_groups"] != checks["multi_payment_reporting_groups"]:
		raise RuntimeError(f"Multiple-payment preservation failed: {checks}")
	if checks["clean_payment_total"] != checks["reporting_payment_total"]:
		raise RuntimeError(f"Payment total reconciliation failed: {checks}")
	return checks


def build_reporting_tables() -> dict[str, Any]:
	"""Rebuild derived reporting tables from CLEAN data in one transaction."""
	with get_engine().begin() as connection:
		_execute_schema(connection)
		_ensure_duration_anomaly_columns(connection)
		for table in REPORTING_TABLES:
			connection.execute(text(f"DELETE FROM {table}"))

		connection.execute(text("""
			INSERT INTO dim_flights
			(flight_id, airline, source, destination, departure_time, arrival_time,
			 duration_minutes, duration_anomaly_flag, duration_anomaly_reason,
			 flight_date, route)
			SELECT flight_id, airline, source, destination, departure_time, arrival_time,
			       duration_minutes,
			       CASE WHEN duration_minutes IS NOT NULL
			                  AND (duration_minutes < stats.lower_threshold
			                       OR duration_minutes > stats.upper_threshold)
			            THEN 1 ELSE 0 END,
			       CASE WHEN duration_minutes IS NOT NULL
			                  AND (duration_minutes < stats.lower_threshold
			                       OR duration_minutes > stats.upper_threshold)
			            THEN 'DURATION_OUTLIER' ELSE NULL END,
			       DATE(departure_time), CONCAT(source, '->', destination)
			FROM clean_flights
			CROSS JOIN (
				SELECT AVG(duration_minutes) - 2 * STDDEV_POP(duration_minutes) AS lower_threshold,
				       AVG(duration_minutes) + 2 * STDDEV_POP(duration_minutes) AS upper_threshold
				FROM clean_flights
				WHERE duration_minutes IS NOT NULL
			) stats
		"""))
		connection.execute(text("""
			INSERT INTO dim_passengers
			(passenger_id, age, age_group, gender, date_of_birth)
			SELECT passenger_id, age,
			       CASE
			           WHEN age IS NULL THEN 'UNKNOWN'
			           WHEN age < 18 THEN 'UNDER_18'
			           WHEN age < 36 THEN '18_35'
			           WHEN age < 56 THEN '36_55'
			           ELSE '56_PLUS'
			       END,
			       gender, date_of_birth
			FROM clean_passengers
		"""))
		connection.execute(text("""
			INSERT INTO booking_payment_summary
			(booking_id, payment_count, total_payment_amount, payment_method_summary, payment_category)
			SELECT b.booking_id,
			       COUNT(p.payment_id),
			       COALESCE(SUM(p.amount), 0),
			       GROUP_CONCAT(DISTINCT p.payment_method ORDER BY p.payment_method SEPARATOR ', '),
			       CASE
			           WHEN COUNT(p.payment_id) = 0 THEN 'UNPAID'
			           WHEN COUNT(p.payment_id) = 1 THEN 'SINGLE_PAYMENT'
			           ELSE 'MULTIPLE_PAYMENTS'
			       END
			FROM clean_bookings b
			LEFT JOIN clean_payments p ON p.booking_id = b.booking_id
			GROUP BY b.booking_id
		"""))
		connection.execute(text("""
			INSERT INTO fact_bookings
			(booking_id, passenger_id, flight_id, airline, source, destination,
			 booking_date, booking_year, booking_month, booking_month_name, booking_day,
			 status, seat_number, departure_time, arrival_time, duration_minutes,
			 flight_date, route, passenger_age_group, payment_count,
			 total_payment_amount, payment_method_summary, payment_category,
			 flight_match_status)
			SELECT b.booking_id, b.passenger_id, b.flight_id,
			       f.airline, f.source, f.destination,
			       b.booking_date, YEAR(b.booking_date), MONTH(b.booking_date),
			       MONTHNAME(b.booking_date), DAY(b.booking_date),
			       b.status, b.seat_number, f.departure_time, f.arrival_time,
			       f.duration_minutes, DATE(f.departure_time),
			       CASE WHEN f.flight_id IS NULL THEN NULL ELSE CONCAT(f.source, '->', f.destination) END,
			       p.age_group, ps.payment_count, ps.total_payment_amount,
			       ps.payment_method_summary, ps.payment_category,
			       CASE WHEN f.flight_id IS NULL THEN 'UNMATCHED_FLIGHT' ELSE 'MATCHED' END
			FROM clean_bookings b
			LEFT JOIN dim_flights f ON f.flight_id = b.flight_id
			LEFT JOIN dim_passengers p ON p.passenger_id = b.passenger_id
			JOIN booking_payment_summary ps ON ps.booking_id = b.booking_id
		"""))

		counts = {table: int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()) for table in REPORTING_TABLES}
		checks = _verify_reporting_tables(connection)
		return {"counts": counts, "checks": checks}


if __name__ == "__main__":
	argparse.ArgumentParser(description="Build ASG Airlines reporting tables.").parse_args()
	print(json.dumps(build_reporting_tables(), indent=2))
