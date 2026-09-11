"""Read-only validation of ASG Airlines RAW, CLEAN, audit, and quarantine data."""

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from .db import get_engine


RAW_EXPECTED = {
	"raw_flights": 1020,
	"raw_bookings": 1012,
	"raw_passengers": 1039,
	"raw_payments": 1000,
}


def _scalar(connection: Any, sql: str, parameters: dict[str, Any] | None = None) -> int:
	return int(connection.execute(text(sql), parameters or {}).scalar_one())


def _rows(connection: Any, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
	return [dict(row) for row in connection.execute(text(sql), parameters or {}).mappings()]


def _check(name: str, value: Any, severity: str = "PASS", detail: str = "") -> dict[str, Any]:
	return {"name": name, "value": value, "severity": severity, "detail": detail}


def _key_checks(connection: Any) -> list[dict[str, Any]]:
	checks = []
	for table, column in [
		("clean_flights", "flight_id"), ("clean_bookings", "booking_id"),
		("clean_passengers", "passenger_id"), ("clean_payments", "payment_id"),
	]:
		missing = _scalar(connection, f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL OR TRIM({column}) = ''")
		duplicates = _scalar(connection, f"SELECT COUNT(*) FROM (SELECT {column} FROM {table} GROUP BY {column} HAVING COUNT(*) > 1) d")
		checks.append(_check(f"{table}.{column} missing", missing, "FAIL" if missing else "PASS"))
		checks.append(_check(f"{table}.{column} duplicate groups", duplicates, "FAIL" if duplicates else "PASS"))

	booking_states = _scalar(connection, """
		SELECT COUNT(*) FROM (
			SELECT booking_id FROM clean_bookings
			GROUP BY booking_id HAVING COUNT(DISTINCT COALESCE(status, '')) > 1
		) conflicts
	""")
	checks.append(_check("clean_bookings conflicting booking states", booking_states, "WARNING" if booking_states else "PASS", "Clean booking IDs are unique; this checks for any residual state conflicts."))
	return checks


def _payment_validation(connection: Any) -> dict[str, Any]:
	counts = {
		"total_clean_payment_rows": _scalar(connection, "SELECT COUNT(*) FROM clean_payments"),
		"bookings_with_zero_payment_rows": _scalar(connection, """
			SELECT COUNT(*) FROM clean_bookings b
			LEFT JOIN clean_payments p ON p.booking_id = b.booking_id
			WHERE p.payment_id IS NULL
		"""),
		"bookings_with_one_payment_row": _scalar(connection, """
			SELECT COUNT(*) FROM (
				SELECT b.booking_id FROM clean_bookings b
				LEFT JOIN clean_payments p ON p.booking_id = b.booking_id
				GROUP BY b.booking_id HAVING COUNT(p.payment_id) = 1
			) one_payment
		"""),
		"bookings_with_multiple_payment_rows": _scalar(connection, """
			SELECT COUNT(*) FROM (
				SELECT booking_id FROM clean_payments
				GROUP BY booking_id HAVING COUNT(*) > 1
			) multiple_payment
		"""),
		"payment_rows_belonging_to_multiple_payment_bookings": _scalar(connection, """
			SELECT COUNT(*) FROM clean_payments p
			JOIN (
				SELECT booking_id FROM clean_payments GROUP BY booking_id HAVING COUNT(*) > 1
			) m ON m.booking_id = p.booking_id
		"""),
		"orphan_booking_references": _scalar(connection, """
			SELECT COUNT(*) FROM clean_payments p
			LEFT JOIN clean_bookings b ON b.booking_id = p.booking_id
			WHERE p.booking_id IS NOT NULL AND b.booking_id IS NULL
		"""),
		"missing_booking_id_payments": _scalar(connection, "SELECT COUNT(*) FROM clean_payments WHERE booking_id IS NULL OR TRIM(booking_id) = ''"),
		"invalid_or_non_numeric_amount_values": _scalar(connection, "SELECT COUNT(*) FROM clean_payments WHERE amount IS NULL"),
		"zero_amounts": _scalar(connection, "SELECT COUNT(*) FROM clean_payments WHERE amount = 0"),
		"negative_amounts": _scalar(connection, "SELECT COUNT(*) FROM clean_payments WHERE amount < 0"),
	}
	counts["statistical_anomalies"] = _scalar(connection, """
		SELECT COUNT(*) FROM (
			SELECT p.payment_id, p.amount,
				AVG(p.amount) OVER (PARTITION BY f.source, f.destination) AS route_mean,
				STDDEV_POP(p.amount) OVER (PARTITION BY f.source, f.destination) AS route_std
			FROM clean_payments p
			JOIN clean_bookings b ON b.booking_id = p.booking_id
			JOIN clean_flights f ON f.flight_id = b.flight_id
			WHERE p.amount IS NOT NULL
		) route_amounts
		WHERE route_std IS NOT NULL AND amount > route_mean + (3 * route_std)
	""")
	return counts


def _relationship_validation(connection: Any) -> dict[str, dict[str, int]]:
	relationships = {
		"bookings_to_flights": ("clean_bookings", "flight_id", "clean_flights", "flight_id"),
		"bookings_to_passengers": ("clean_bookings", "passenger_id", "clean_passengers", "passenger_id"),
		"payments_to_bookings": ("clean_payments", "booking_id", "clean_bookings", "booking_id"),
	}
	result = {}
	for name, (child, child_key, parent, parent_key) in relationships.items():
		missing = _scalar(connection, f"SELECT COUNT(*) FROM {child} WHERE {child_key} IS NULL OR TRIM({child_key}) = ''")
		unmatched = _scalar(connection, f"""
			SELECT COUNT(*) FROM {child} c
			LEFT JOIN {parent} p ON p.{parent_key} = c.{child_key}
			WHERE c.{child_key} IS NOT NULL AND TRIM(c.{child_key}) <> '' AND p.{parent_key} IS NULL
		""")
		matched = _scalar(connection, f"""
			SELECT COUNT(*) FROM {child} c
			JOIN {parent} p ON p.{parent_key} = c.{child_key}
		""")
		result[name] = {"matched": matched, "unmatched_orphan": unmatched, "missing_reference": missing}
	return result


def _flight_validation(connection: Any) -> dict[str, Any]:
	result = {
		"missing_flight_id": _scalar(connection, "SELECT COUNT(*) FROM clean_flights WHERE flight_id IS NULL OR TRIM(flight_id) = ''"),
		"duplicate_flight_id_groups": _scalar(connection, "SELECT COUNT(*) FROM (SELECT flight_id FROM clean_flights GROUP BY flight_id HAVING COUNT(*) > 1) d"),
		"missing_airline": _scalar(connection, "SELECT COUNT(*) FROM clean_flights WHERE airline IS NULL OR TRIM(airline) = ''"),
		"invalid_timestamps": _scalar(connection, "SELECT COUNT(*) FROM clean_flights WHERE departure_time IS NULL OR arrival_time IS NULL"),
		"arrival_before_departure": _scalar(connection, "SELECT COUNT(*) FROM clean_flights WHERE arrival_time < departure_time"),
		"duration_over_12_hours": _scalar(connection, "SELECT COUNT(*) FROM clean_flights WHERE duration_minutes > 720"),
		"missing_source_or_destination": _scalar(connection, "SELECT COUNT(*) FROM clean_flights WHERE source IS NULL OR destination IS NULL OR TRIM(source) = '' OR TRIM(destination) = ''"),
		"same_source_destination": _scalar(connection, "SELECT COUNT(*) FROM clean_flights WHERE source = destination"),
		"ambiguous_prefixes": _scalar(connection, """
			SELECT COUNT(*) FROM (
				SELECT LEFT(flight_id, 2) AS prefix FROM raw_flights
				WHERE flight_id IS NOT NULL AND TRIM(flight_id) <> ''
				GROUP BY LEFT(flight_id, 2) HAVING COUNT(DISTINCT NULLIF(TRIM(airline), '')) > 1
			) prefixes
		"""),
	}
	try:
		result["airport_reference_available"] = bool(_scalar(connection, "SELECT COUNT(*) FROM airport_reference WHERE active = TRUE") > 0)
	except Exception:
		result["airport_reference_available"] = False
	return result


def _booking_validation(connection: Any) -> dict[str, Any]:
	return {
		"duplicate_booking_id_groups": _scalar(connection, "SELECT COUNT(*) FROM (SELECT booking_id FROM clean_bookings GROUP BY booking_id HAVING COUNT(*) > 1) d"),
		"missing_booking_id": _scalar(connection, "SELECT COUNT(*) FROM clean_bookings WHERE booking_id IS NULL OR TRIM(booking_id) = ''"),
		"invalid_booking_dates": _scalar(connection, "SELECT COUNT(*) FROM clean_bookings WHERE booking_date IS NULL"),
		"unexpected_status_values": _scalar(connection, "SELECT COUNT(*) FROM clean_bookings WHERE status IS NULL OR TRIM(status) = '' OR UPPER(status) NOT IN ('CONFIRMED', 'CANCELLED', 'PENDING')"),
		"passport_conflicts": _scalar(connection, """
			SELECT COUNT(*) FROM (
				SELECT passport_number FROM clean_bookings
				WHERE passport_number IS NOT NULL AND TRIM(passport_number) <> ''
				GROUP BY passport_number HAVING COUNT(DISTINCT passenger_id) > 1
			) conflicts
		"""),
		"seat_conflicts": _scalar(connection, """
			SELECT COUNT(*) FROM (
				SELECT flight_id, seat_number FROM clean_bookings
				WHERE flight_id IS NOT NULL AND seat_number IS NOT NULL
				GROUP BY flight_id, seat_number HAVING COUNT(DISTINCT passenger_id) > 1
			) conflicts
		"""),
		"emergency_contact_format_candidates": _scalar(connection, """
			SELECT COUNT(*) FROM clean_bookings
			WHERE emergency_contact_phone IS NOT NULL
			AND (CHAR_LENGTH(emergency_contact_phone) < 7
				 OR emergency_contact_phone REGEXP '[^+0-9 ()-]')
		"""),
	}


def _passenger_validation(connection: Any) -> dict[str, Any]:
	return {
		"duplicate_passenger_id_groups": _scalar(connection, "SELECT COUNT(*) FROM (SELECT passenger_id FROM clean_passengers GROUP BY passenger_id HAVING COUNT(*) > 1) d"),
		"missing_passenger_id": _scalar(connection, "SELECT COUNT(*) FROM clean_passengers WHERE passenger_id IS NULL OR TRIM(passenger_id) = ''"),
		"invalid_age": _scalar(connection, "SELECT COUNT(*) FROM clean_passengers WHERE age IS NULL OR age < 0 OR age > 120"),
		"invalid_gender_candidates": _scalar(connection, "SELECT COUNT(*) FROM clean_passengers WHERE gender IS NOT NULL AND UPPER(gender) NOT IN ('M', 'F')"),
		"invalid_email_candidates": _scalar(connection, "SELECT COUNT(*) FROM clean_passengers WHERE email IS NOT NULL AND (LOCATE('@', email) = 0 OR LOCATE('.', email, LOCATE('@', email) + 2) = 0)"),
		"invalid_phone_candidates": _scalar(connection, "SELECT COUNT(*) FROM clean_passengers WHERE phone IS NOT NULL AND (CHAR_LENGTH(phone) < 7 OR phone REGEXP '[^+0-9 ()-]')"),
		"invalid_aadhaar_candidates": _scalar(connection, "SELECT COUNT(*) FROM clean_passengers WHERE aadhaar_id IS NOT NULL AND (CHAR_LENGTH(aadhaar_id) <> 12 OR aadhaar_id REGEXP '[^0-9]')"),
		"invalid_date_of_birth": _scalar(connection, "SELECT COUNT(*) FROM clean_passengers WHERE date_of_birth IS NULL OR date_of_birth > CURRENT_DATE"),
	}


def _audit_validation(connection: Any) -> list[dict[str, Any]]:
	return _rows(connection, """
		SELECT entity_name, rule_identifier, action, COUNT(*) AS count
		FROM audit_log
		GROUP BY entity_name, rule_identifier, action
		ORDER BY entity_name, rule_identifier, action
	""")


def _quarantine_validation(connection: Any) -> dict[str, Any]:
	by_reason = _rows(connection, """
		SELECT entity_name, issue_category, COUNT(*) AS count
		FROM quarantine_records
		GROUP BY entity_name, issue_category
		ORDER BY entity_name, issue_category
	""")
	missing_trace = _scalar(connection, """
		SELECT COUNT(*) FROM quarantine_records
		WHERE entity_name IS NULL OR TRIM(entity_name) = ''
		OR issue_category IS NULL OR TRIM(issue_category) = ''
		OR reason IS NULL OR TRIM(reason) = ''
		OR run_id IS NULL OR TRIM(run_id) = ''
		OR batch_id IS NULL OR TRIM(batch_id) = ''
		OR original_payload IS NULL
	""")
	return {"by_reason": by_reason, "records_missing_required_traceability": missing_trace}


def _reconciliation(connection: Any) -> list[dict[str, Any]]:
	result = []
	for entity, raw, clean in [("flights", "raw_flights", "clean_flights"), ("bookings", "raw_bookings", "clean_bookings"), ("passengers", "raw_passengers", "clean_passengers"), ("payments", "raw_payments", "clean_payments")]:
		raw_count = _scalar(connection, f"SELECT COUNT(*) FROM {raw}")
		clean_count = _scalar(connection, f"SELECT COUNT(*) FROM {clean}")
		quarantine_count = _scalar(connection, "SELECT COUNT(*) FROM quarantine_records WHERE entity_name = :entity", {"entity": entity})
		result.append({"entity": entity, "raw_count": raw_count, "clean_count": clean_count, "quarantined_count": quarantine_count, "raw_minus_clean": raw_count - clean_count, "clean_plus_quarantine": clean_count + quarantine_count})
	return result


def validate_database() -> dict[str, Any]:
	with get_engine().connect() as connection:
		raw_counts = {table: _scalar(connection, f"SELECT COUNT(*) FROM {table}") for table in RAW_EXPECTED}
		raw_checks = [_check(table, count, "PASS" if count == RAW_EXPECTED[table] else "FAIL", f"expected {RAW_EXPECTED[table]}") for table, count in raw_counts.items()]
		clean_counts = {table: _scalar(connection, f"SELECT COUNT(*) FROM {table}") for table in ("clean_flights", "clean_bookings", "clean_passengers", "clean_payments")}
		return {
			"raw_counts": raw_counts,
			"raw_checks": raw_checks,
			"clean_counts": clean_counts,
			"key_checks": _key_checks(connection),
			"payments": _payment_validation(connection),
			"relationships": _relationship_validation(connection),
			"flights": _flight_validation(connection),
			"bookings": _booking_validation(connection),
			"passengers": _passenger_validation(connection),
			"audit_by_rule": _audit_validation(connection),
			"quarantine": _quarantine_validation(connection),
			"reconciliation": _reconciliation(connection),
		}


def _status(result: dict[str, Any]) -> str:
	if any(check["severity"] == "FAIL" for check in result["raw_checks"] + result["key_checks"]):
		return "FAIL"
	return "WARNING" if result["flights"]["ambiguous_prefixes"] or not result["flights"]["airport_reference_available"] or result["quarantine"]["records_missing_required_traceability"] else "PASS"


def _report(result: dict[str, Any]) -> str:
	status = _status(result)
	lines = ["# ASG Airlines Phase 4B Validation Report", "", f"**Final status:** `{status}`", "", "## RAW Invariants", "", "| Table | Actual | Expected | Status |", "| --- | ---: | ---: | --- |"]
	for check in result["raw_checks"]:
		lines.append(f"| `{check['name']}` | {check['value']} | {check['detail'].replace('expected ', '')} | {check['severity']} |")
	lines.extend(["", "## CLEAN Counts", "", "| Table | Rows |", "| --- | ---: |"])
	for table, count in result["clean_counts"].items():
		lines.append(f"| `{table}` | {count} |")
	lines.extend(["", "## Key Checks", "", "| Check | Value | Status |", "| --- | ---: | --- |"])
	for check in result["key_checks"]:
		lines.append(f"| {check['name']} | {check['value']} | {check['severity']} |")
	lines.extend(["", "## Payment Validation", "", "| Metric | Count |", "| --- | ---: |"])
	for name, count in result["payments"].items():
		lines.append(f"| {name.replace('_', ' ')} | {count} |")
	lines.extend(["", "## Relationships", "", "| Relationship | Matched | Unmatched/orphan | Missing reference |", "| --- | ---: | ---: | ---: |"])
	for name, values in result["relationships"].items():
		lines.append(f"| {name} | {values['matched']} | {values['unmatched_orphan']} | {values['missing_reference']} |")
	lines.extend(["", "## Flight, Booking, and Passenger Checks", "", "```json", json.dumps({key: result[key] for key in ("flights", "bookings", "passengers")}, indent=2, default=str), "```", "", "## Audit Summary", "", "| Entity | Rule | Action | Count |", "| --- | --- | --- | ---: |"])
	for row in result["audit_by_rule"]:
		lines.append(f"| {row['entity_name']} | {row['rule_identifier']} | {row['action']} | {row['count']} |")
	lines.extend(["", "## Quarantine Summary", "", "| Entity | Category | Count |", "| --- | --- | ---: |"])
	for row in result["quarantine"]["by_reason"]:
		lines.append(f"| {row['entity_name']} | {row['issue_category']} | {row['count']} |")
	lines.append(f"\nRecords missing required quarantine traceability fields: {result['quarantine']['records_missing_required_traceability']}")
	lines.extend(["", "## Reconciliation", "", "| Entity | RAW | CLEAN | Quarantine | RAW-CLEAN | CLEAN+Quarantine |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
	for row in result["reconciliation"]:
		lines.append(f"| {row['entity']} | {row['raw_count']} | {row['clean_count']} | {row['quarantined_count']} | {row['raw_minus_clean']} | {row['clean_plus_quarantine']} |")
	lines.extend(["", "## Known Limitations and Warnings", "", "- The airport reference table is unavailable or empty, so airport validity is reported as a limitation rather than inferred from observed values.", "- Airline prefixes remain ambiguous because named airlines and `UNKNOWN` coexist; no airline mapping is invented.", "- Clean payment amounts are typed DECIMAL values, so original non-numeric placeholders cannot be distinguished from other NULL amounts at this phase.", "- Validation is read-only and does not add foreign keys or change any data.", ""])
	return "\n".join(lines)


def main() -> None:
	parser = argparse.ArgumentParser(description="Validate ASG Airlines data without modifying the database.")
	parser.add_argument("--report", default="docs/validation_report.md")
	args = parser.parse_args()
	result = validate_database()
	Path(args.report).write_text(_report(result), encoding="utf-8")
	print(json.dumps({"status": _status(result), "raw_counts": result["raw_counts"], "clean_counts": result["clean_counts"], "payments": result["payments"], "report": args.report}, indent=2, default=str))


if __name__ == "__main__":
	main()
