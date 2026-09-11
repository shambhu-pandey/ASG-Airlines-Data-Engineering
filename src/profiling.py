"""Read-only profiling for the ASG Airlines RAW tables."""

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from .db import get_engine


TABLE_COLUMNS = {
	"raw_flights": ["flight_id", "airline", "source", "destination", "departure_time", "arrival_time", "duration"],
	"raw_bookings": ["booking_id", "passenger_id", "flight_id", "booking_date", "status", "passport_number", "seat_number", "emergency_contact_name", "emergency_contact_phone"],
	"raw_passengers": ["passenger_id", "first_name", "last_name", "age", "gender", "email", "phone", "aadhaar_id", "date_of_birth"],
	"raw_payments": ["payment_id", "booking_id", "amount", "payment_method"],
}

def _text_values(values: pd.Series) -> pd.Series:
	return values.astype("string").str.strip()


def _blank_mask(values: pd.Series) -> pd.Series:
	return values.isna() | _text_values(values).eq("")


def _examples(values: pd.Series, limit: int = 3) -> list[str]:
	return [str(value) for value in values.dropna().drop_duplicates().head(limit).tolist()]


def _issue(
	issues: list[dict[str, Any]],
	issue: str,
	table: str,
	column: str,
	count: int,
	total: int,
	examples: Any,
	category: str,
) -> None:
	if count <= 0:
		return
	issues.append({
		"issue": issue,
		"table": table,
		"column": column,
		"count": int(count),
		"percentage": round((count / total) * 100, 2) if total else 0,
		"examples": ", ".join(str(value) for value in examples) if not isinstance(examples, str) else examples,
		"recommended_handling": category,
	})


def _parse_datetime(values: pd.Series) -> pd.Series:
	return pd.to_datetime(values, errors="coerce", format="mixed")


def _parse_number(values: pd.Series) -> pd.Series:
	return pd.to_numeric(values, errors="coerce")


def _profile_common(
	data: dict[str, pd.DataFrame],
	issues: list[dict[str, Any]],
	metrics: dict[str, Any],
) -> None:
	for table_name, columns in TABLE_COLUMNS.items():
		frame = data[table_name]
		metrics[table_name] = {"row_count": len(frame), "columns": {}}
		for column in columns:
			values = frame[column]
			blank_count = int(_blank_mask(values).sum())
			metrics[table_name]["columns"][column] = {
				"blank_count": blank_count,
				"distinct_count": int(_text_values(values).replace("<NA>", pd.NA).dropna().nunique()),
			}
			_issue(issues, "Missing or blank source values", table_name, column, blank_count, len(frame), _examples(values[_blank_mask(values)]), "PROFILE_ONLY")


def _profile_flights(frame: pd.DataFrame, issues: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
	total = len(frame)
	flight_ids = _text_values(frame["flight_id"])
	valid_ids = flight_ids[~_blank_mask(frame["flight_id"])]
	duplicate_ids = valid_ids[valid_ids.duplicated(keep=False)]
	malformed_ids = valid_ids[~valid_ids.str.fullmatch(r"[A-Za-z0-9]+", na=False)]
	flight_prefixes = valid_ids.str[:2]
	prefix_airline_counts = frame.assign(_prefix=flight_ids.str[:2]).loc[~_blank_mask(frame["flight_id"])].groupby("_prefix")["airline"].nunique()
	conflicting_prefixes = prefix_airline_counts[prefix_airline_counts > 1].index

	_issue(issues, "Duplicate flight IDs", "raw_flights", "flight_id", int(duplicate_ids.nunique()), total, _examples(duplicate_ids), "NEEDS_MANUAL_REVIEW")
	_issue(issues, "Malformed flight ID characters", "raw_flights", "flight_id", int(malformed_ids.nunique()), total, _examples(malformed_ids), "NEEDS_VALIDATION")
	_issue(issues, "Flight prefix maps to multiple airlines", "raw_flights", "flight_id", len(conflicting_prefixes), total, list(conflicting_prefixes), "NEEDS_MANUAL_REVIEW")

	departure = _parse_datetime(frame["departure_time"])
	arrival = _parse_datetime(frame["arrival_time"])
	departure_present = ~_blank_mask(frame["departure_time"])
	arrival_present = ~_blank_mask(frame["arrival_time"])
	unparseable_departure = departure_present & departure.isna()
	unparseable_arrival = arrival_present & arrival.isna()
	arrival_before_departure = departure.notna() & arrival.notna() & (arrival < departure)
	duration_text = _text_values(frame["duration"])
	duration_values = _parse_number(frame["duration"])
	duration_present = ~_blank_mask(frame["duration"])
	formula_duration = duration_present & duration_text.str.startswith("=", na=False)
	nonnumeric_duration = duration_present & duration_values.isna() & ~formula_duration
	long_duration = duration_values > 12 * 60
	suspicious_duration = duration_values.notna() & (duration_values <= 0)
	same_airports = (~_blank_mask(frame["source"])) & (~_blank_mask(frame["destination"])) & (_text_values(frame["source"]) == _text_values(frame["destination"]))

	_issue(issues, "Unparseable departure timestamps", "raw_flights", "departure_time", int(unparseable_departure.sum()), total, _examples(frame.loc[unparseable_departure, "departure_time"]), "NEEDS_VALIDATION")
	_issue(issues, "Unparseable arrival timestamps", "raw_flights", "arrival_time", int(unparseable_arrival.sum()), total, _examples(frame.loc[unparseable_arrival, "arrival_time"]), "NEEDS_VALIDATION")
	_issue(issues, "Arrival earlier than departure", "raw_flights", "arrival_time", int(arrival_before_departure.sum()), total, _examples(frame.loc[arrival_before_departure, "flight_id"]), "NEEDS_VALIDATION")
	_issue(issues, "Same source and destination airport", "raw_flights", "source/destination", int(same_airports.sum()), total, _examples(frame.loc[same_airports, "flight_id"]), "NEEDS_VALIDATION")
	_issue(issues, "Formula duration strings", "raw_flights", "duration", int(formula_duration.sum()), total, _examples(frame.loc[formula_duration, "duration"]), "NEEDS_CLEANING")
	_issue(issues, "Non-numeric duration values", "raw_flights", "duration", int(nonnumeric_duration.sum()), total, _examples(frame.loc[nonnumeric_duration, "duration"]), "NEEDS_VALIDATION")
	_issue(issues, "Duration exceeds 12-hour cap", "raw_flights", "duration", int(long_duration.sum()), total, _examples(frame.loc[long_duration, "duration"]), "NEEDS_VALIDATION")
	_issue(issues, "Non-positive duration values", "raw_flights", "duration", int(suspicious_duration.sum()), total, _examples(frame.loc[suspicious_duration, "duration"]), "NEEDS_VALIDATION")

	metrics["flight_analysis"] = {
		"distinct_prefixes": int(flight_prefixes.nunique()),
		"prefix_distribution": flight_prefixes.value_counts().head(20).to_dict(),
		"parseable_departures": int(departure.notna().sum()),
		"parseable_arrivals": int(arrival.notna().sum()),
		"possible_collision_ids": _examples(duplicate_ids, 10),
	}


def _profile_bookings(
	frame: pd.DataFrame,
	flights: pd.DataFrame,
	passengers: pd.DataFrame,
	issues: list[dict[str, Any]],
	metrics: dict[str, Any],
) -> None:
	total = len(frame)
	booking_ids = _text_values(frame["booking_id"])
	valid_booking_ids = booking_ids[~_blank_mask(frame["booking_id"])]
	duplicate_booking_ids = valid_booking_ids[valid_booking_ids.duplicated(keep=False)]
	malformed_booking_ids = valid_booking_ids[~valid_booking_ids.str.fullmatch(r"[A-Za-z]+[A-Za-z0-9]*", na=False)]
	duplicate_rows = frame[TABLE_COLUMNS["raw_bookings"]].astype("string").fillna("").duplicated(keep=False)
	conflicting_booking_ids = frame.assign(_booking_id=booking_ids).groupby("_booking_id", dropna=True)[TABLE_COLUMNS["raw_bookings"]].nunique().gt(1).any(axis=1)
	conflicting_booking_ids = conflicting_booking_ids[conflicting_booking_ids.index != ""]

	_issue(issues, "Duplicate booking IDs", "raw_bookings", "booking_id", int(duplicate_booking_ids.nunique()), total, _examples(duplicate_booking_ids), "NEEDS_VALIDATION")
	_issue(issues, "Malformed booking IDs", "raw_bookings", "booking_id", int(malformed_booking_ids.nunique()), total, _examples(malformed_booking_ids), "NEEDS_VALIDATION")
	_issue(issues, "Exact duplicate booking rows", "raw_bookings", "booking_id", int(duplicate_rows.sum()), total, _examples(frame.loc[duplicate_rows, "booking_id"]), "NEEDS_CLEANING")
	_issue(issues, "Booking IDs with conflicting values", "raw_bookings", "booking_id", int(conflicting_booking_ids.sum()), total, list(conflicting_booking_ids[conflicting_booking_ids].index[:3]), "NEEDS_MANUAL_REVIEW")

	flight_values = _text_values(frame["flight_id"])
	passenger_values = _text_values(frame["passenger_id"])
	raw_flight_ids = set(_text_values(flights["flight_id"])[~_blank_mask(flights["flight_id"])])
	raw_passenger_ids = set(_text_values(passengers["passenger_id"])[~_blank_mask(passengers["passenger_id"])])
	orphan_flights = (~_blank_mask(frame["flight_id"])) & ~flight_values.isin(raw_flight_ids)
	orphan_passengers = (~_blank_mask(frame["passenger_id"])) & ~passenger_values.isin(raw_passenger_ids)
	_issue(issues, "Booking flight IDs missing from raw flights", "raw_bookings", "flight_id", int(orphan_flights.sum()), total, _examples(frame.loc[orphan_flights, "flight_id"]), "NEEDS_VALIDATION")
	_issue(issues, "Booking passenger IDs missing from raw passengers", "raw_bookings", "passenger_id", int(orphan_passengers.sum()), total, _examples(frame.loc[orphan_passengers, "passenger_id"]), "NEEDS_VALIDATION")

	booking_dates = _parse_datetime(frame["booking_date"])
	date_present = ~_blank_mask(frame["booking_date"])
	future_dates = booking_dates.notna() & (booking_dates.dt.date > pd.Timestamp.now().date())
	status_values = _text_values(frame["status"])
	passport_values = _text_values(frame["passport_number"])
	lowercase_passports = passport_values.notna() & passport_values.ne("<NA>") & passport_values.ne(passport_values.str.upper())
	passport_passenger_counts = frame.assign(_passport=passport_values).loc[~_blank_mask(frame["passport_number"])].groupby("_passport")["passenger_id"].nunique()
	passport_conflicts = passport_passenger_counts[passport_passenger_counts > 1]
	seat_values = _text_values(frame["seat_number"])
	seat_rows = frame.loc[(~_blank_mask(frame["flight_id"])) & (~_blank_mask(frame["seat_number"]))].assign(_flight=flight_values, _seat=seat_values)
	seat_conflicts = seat_rows.groupby(["_flight", "_seat"])["passenger_id"].nunique()
	seat_conflicts = seat_conflicts[seat_conflicts > 1]
	contact_names = _text_values(frame["emergency_contact_name"])
	invalid_names = contact_names[~_blank_mask(frame["emergency_contact_name"]) & ~contact_names.str.fullmatch(r"[A-Za-z][A-Za-z .'-]*", na=False)]
	contact_phones = _text_values(frame["emergency_contact_phone"])
	invalid_phones = contact_phones[~_blank_mask(frame["emergency_contact_phone"]) & ~contact_phones.str.fullmatch(r"\+?[0-9][0-9 ()-]{6,}", na=False)]

	_issue(issues, "Unparseable booking dates", "raw_bookings", "booking_date", int((date_present & booking_dates.isna()).sum()), total, _examples(frame.loc[date_present & booking_dates.isna(), "booking_date"]), "NEEDS_VALIDATION")
	_issue(issues, "Future booking dates", "raw_bookings", "booking_date", int(future_dates.sum()), total, _examples(frame.loc[future_dates, "booking_date"]), "NEEDS_VALIDATION")
	_issue(issues, "Lowercase passport values", "raw_bookings", "passport_number", int(lowercase_passports.sum()), total, _examples(frame.loc[lowercase_passports, "passport_number"]), "NEEDS_CLEANING")
	_issue(issues, "Passport associated with multiple passengers", "raw_bookings", "passport_number", len(passport_conflicts), total, list(passport_conflicts.index[:3]), "NEEDS_MANUAL_REVIEW")
	_issue(issues, "Conflicting seat assignments", "raw_bookings", "seat_number", len(seat_conflicts), total, [f"{flight}/{seat}" for flight, seat in seat_conflicts.index[:3]], "NEEDS_MANUAL_REVIEW")
	_issue(issues, "Emergency contact name format candidates", "raw_bookings", "emergency_contact_name", len(invalid_names), total, _examples(invalid_names), "NEEDS_VALIDATION")
	_issue(issues, "Emergency contact phone format candidates", "raw_bookings", "emergency_contact_phone", len(invalid_phones), total, _examples(invalid_phones), "NEEDS_VALIDATION")

	metrics["booking_analysis"] = {
		"distinct_statuses": sorted(value for value in status_values.dropna().unique().tolist()),
		"parseable_booking_dates": int(booking_dates.notna().sum()),
		"passport_conflict_count": len(passport_conflicts),
		"seat_conflict_count": len(seat_conflicts),
	}


def _profile_passengers(frame: pd.DataFrame, issues: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
	total = len(frame)
	ages = _parse_number(frame["age"])
	age_present = ~_blank_mask(frame["age"])
	invalid_ages = age_present & (ages.isna() | (ages < 0) | (ages > 120))
	emails = _text_values(frame["email"])
	invalid_emails = ~_blank_mask(frame["email"]) & ~emails.str.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", na=False)
	phones = _text_values(frame["phone"])
	invalid_phones = ~_blank_mask(frame["phone"]) & ~phones.str.fullmatch(r"\+?[0-9][0-9 ()-]{6,}", na=False)
	aadhaar = _text_values(frame["aadhaar_id"])
	invalid_aadhaar = ~_blank_mask(frame["aadhaar_id"]) & ~aadhaar.str.fullmatch(r"[0-9]{12}", na=False)
	dates_of_birth = _parse_datetime(frame["date_of_birth"])
	future_dates = dates_of_birth.notna() & (dates_of_birth.dt.date > pd.Timestamp.now().date())
	name_columns = ["first_name", "last_name"]
	invalid_names = {column: (~_blank_mask(frame[column]) & ~_text_values(frame[column]).str.fullmatch(r"[A-Za-z][A-Za-z .'-]*", na=False)) for column in name_columns}

	_issue(issues, "Non-numeric or unreasonable ages", "raw_passengers", "age", int(invalid_ages.sum()), total, _examples(frame.loc[invalid_ages, "age"]), "NEEDS_VALIDATION")
	_issue(issues, "Invalid email format candidates", "raw_passengers", "email", int(invalid_emails.sum()), total, _examples(frame.loc[invalid_emails, "email"]), "NEEDS_VALIDATION")
	_issue(issues, "Invalid phone format candidates", "raw_passengers", "phone", int(invalid_phones.sum()), total, _examples(frame.loc[invalid_phones, "phone"]), "NEEDS_VALIDATION")
	_issue(issues, "Invalid Aadhaar format candidates", "raw_passengers", "aadhaar_id", int(invalid_aadhaar.sum()), total, _examples(frame.loc[invalid_aadhaar, "aadhaar_id"]), "NEEDS_VALIDATION")
	_issue(issues, "Unparseable date of birth", "raw_passengers", "date_of_birth", int((~_blank_mask(frame["date_of_birth"]) & dates_of_birth.isna()).sum()), total, _examples(frame.loc[~_blank_mask(frame["date_of_birth"]) & dates_of_birth.isna(), "date_of_birth"]), "NEEDS_VALIDATION")
	_issue(issues, "Future date of birth", "raw_passengers", "date_of_birth", int(future_dates.sum()), total, _examples(frame.loc[future_dates, "date_of_birth"]), "NEEDS_VALIDATION")
	for column, invalid_values in invalid_names.items():
		_issue(issues, "Name format candidates", "raw_passengers", column, int(invalid_values.sum()), total, _examples(frame.loc[invalid_values, column]), "NEEDS_VALIDATION")

	metrics["passenger_analysis"] = {
		"parseable_ages": int(ages.notna().sum()),
		"parseable_dates_of_birth": int(dates_of_birth.notna().sum()),
		"distinct_genders": sorted(value for value in _text_values(frame["gender"]).dropna().unique().tolist()),
	}


def _profile_payments(
	frame: pd.DataFrame,
	bookings: pd.DataFrame,
	flights: pd.DataFrame,
	issues: list[dict[str, Any]],
	metrics: dict[str, Any],
) -> None:
	total = len(frame)
	booking_values = _text_values(frame["booking_id"])
	booking_reference = set(_text_values(bookings["booking_id"])[~_blank_mask(bookings["booking_id"])])
	valid_booking_values = booking_values[~_blank_mask(frame["booking_id"])]
	orphan_payments = (~_blank_mask(frame["booking_id"])) & ~booking_values.isin(booking_reference)
	amounts = _parse_number(frame["amount"])
	amount_present = ~_blank_mask(frame["amount"])
	invalid_amounts = amount_present & amounts.isna()
	negative_amounts = amounts < 0
	zero_amounts = amounts == 0
	amount_mean = amounts.mean()
	amount_std = amounts.std()
	large_amounts = amounts > amount_mean + (3 * amount_std)
	payment_methods = _text_values(frame["payment_method"])

	_issue(issues, "Orphan payment booking IDs", "raw_payments", "booking_id", int(orphan_payments.sum()), total, _examples(frame.loc[orphan_payments, "booking_id"]), "NEEDS_VALIDATION")
	_issue(issues, "Non-numeric or placeholder payment amounts", "raw_payments", "amount", int(invalid_amounts.sum()), total, _examples(frame.loc[invalid_amounts, "amount"]), "NEEDS_CLEANING")
	_issue(issues, "Negative payment amounts", "raw_payments", "amount", int(negative_amounts.sum()), total, _examples(frame.loc[negative_amounts, "amount"]), "NEEDS_MANUAL_REVIEW")
	_issue(issues, "Zero payment amounts", "raw_payments", "amount", int(zero_amounts.sum()), total, _examples(frame.loc[zero_amounts, "amount"]), "NEEDS_MANUAL_REVIEW")
	_issue(issues, "Global payment amount above 3 standard deviations", "raw_payments", "amount", int(large_amounts.sum()), total, _examples(frame.loc[large_amounts, "amount"]), "NEEDS_MANUAL_REVIEW")

	payment_counts = valid_booking_values.value_counts()
	booking_ids = _text_values(bookings["booking_id"])
	booking_count_series = booking_ids[~_blank_mask(bookings["booking_id"])].value_counts()
	zero_payment_bookings = booking_count_series.index.difference(payment_counts.index)
	one_payment_bookings = payment_counts[payment_counts == 1]
	multiple_payment_bookings = payment_counts[payment_counts > 1]
	multiple_payment_rows = int(multiple_payment_bookings.sum())

	booking_to_flight = bookings[["booking_id", "flight_id"]].copy()
	booking_to_flight["booking_id"] = _text_values(booking_to_flight["booking_id"])
	booking_to_flight["flight_id"] = _text_values(booking_to_flight["flight_id"])
	flight_routes = flights[["flight_id", "source", "destination"]].copy()
	flight_routes["flight_id"] = _text_values(flight_routes["flight_id"])
	joined = frame.assign(booking_id=booking_values, amount_numeric=amounts).merge(booking_to_flight, on="booking_id", how="left").merge(flight_routes, on="flight_id", how="left")
	joined["route"] = joined["source"].fillna("<unknown>") + "->" + joined["destination"].fillna("<unknown>")
	valid_joined = joined[joined["amount_numeric"].notna()]
	route_stats = valid_joined.groupby("route")["amount_numeric"].agg(["mean", "std", "count"])
	joined = joined.join(route_stats, on="route", rsuffix="_route")
	route_anomalies = joined["amount_numeric"] > (joined["mean"] + 3 * joined["std"])
	_issue(issues, "Route payment amount above 3 standard deviations", "raw_payments", "amount", int(route_anomalies.fillna(False).sum()), total, _examples(joined.loc[route_anomalies.fillna(False), "payment_id"]), "NEEDS_MANUAL_REVIEW")

	metrics["payment_analysis"] = {
		"parseable_amounts": int(amounts.notna().sum()),
		"valid_payment_methods": sorted(value for value in payment_methods.dropna().unique().tolist()),
		"zero_payment_bookings": int(len(zero_payment_bookings)),
		"one_payment_bookings": int(len(one_payment_bookings)),
		"multiple_payment_bookings": int(len(multiple_payment_bookings)),
		"payment_rows_for_multiple_payment_bookings": multiple_payment_rows,
		"orphan_payment_rows": int(orphan_payments.sum()),
		"route_anomaly_rows": int(route_anomalies.fillna(False).sum()),
	}


def _profile_reference_values(
	data: dict[str, pd.DataFrame],
	issues: list[dict[str, Any]],
) -> None:
	with get_engine().connect() as connection:
		try:
			payment_methods = pd.read_sql_query(text("SELECT payment_method FROM payment_method_reference WHERE active = TRUE"), connection)["payment_method"].astype("string")
		except Exception:
			_issue(issues, "Active payment-method reference table unavailable", "raw_payments", "payment_method", len(data["raw_payments"]), len(data["raw_payments"]), [], "PROFILE_ONLY")
			return
	known_methods = set(payment_methods.dropna().str.strip())
	observed_methods = set(_text_values(data["raw_payments"]["payment_method"]).dropna())
	new_methods = sorted(observed_methods - known_methods)
	_issue(issues, "Payment methods not represented in active reference data", "raw_payments", "payment_method", len(new_methods), len(data["raw_payments"]), new_methods, "NEEDS_VALIDATION")


def profile_raw_data() -> dict[str, Any]:
	"""Profile RAW tables without changing database state."""
	with get_engine().connect() as connection:
		data = {
			table_name: pd.read_sql_query(text(f"SELECT {', '.join(TABLE_COLUMNS[table_name])} FROM {table_name}"), connection)
			for table_name in TABLE_COLUMNS
		}

	issues: list[dict[str, Any]] = []
	metrics: dict[str, Any] = {}
	_profile_common(data, issues, metrics)
	_profile_flights(data["raw_flights"], issues, metrics)
	_profile_bookings(data["raw_bookings"], data["raw_flights"], data["raw_passengers"], issues, metrics)
	_profile_passengers(data["raw_passengers"], issues, metrics)
	_profile_payments(data["raw_payments"], data["raw_bookings"], data["raw_flights"], issues, metrics)

	flight_ids = set(_text_values(data["raw_flights"]["flight_id"])[~_blank_mask(data["raw_flights"]["flight_id"])])
	passenger_ids = set(_text_values(data["raw_passengers"]["passenger_id"])[~_blank_mask(data["raw_passengers"]["passenger_id"])])
	booking_ids = set(_text_values(data["raw_bookings"]["booking_id"])[~_blank_mask(data["raw_bookings"]["booking_id"])])
	booking_flights = _text_values(data["raw_bookings"]["flight_id"])
	booking_passengers = _text_values(data["raw_bookings"]["passenger_id"])
	payment_bookings = _text_values(data["raw_payments"]["booking_id"])
	metrics["cross_table_references"] = {
		"booking_flight_matched": int((~_blank_mask(data["raw_bookings"]["flight_id"]) & booking_flights.isin(flight_ids)).sum()),
		"booking_flight_unmatched": int((~_blank_mask(data["raw_bookings"]["flight_id"]) & ~booking_flights.isin(flight_ids)).sum()),
		"booking_passenger_matched": int((~_blank_mask(data["raw_bookings"]["passenger_id"]) & booking_passengers.isin(passenger_ids)).sum()),
		"booking_passenger_unmatched": int((~_blank_mask(data["raw_bookings"]["passenger_id"]) & ~booking_passengers.isin(passenger_ids)).sum()),
		"payment_booking_matched": int((~_blank_mask(data["raw_payments"]["booking_id"]) & payment_bookings.isin(booking_ids)).sum()),
		"payment_booking_unmatched": int((~_blank_mask(data["raw_payments"]["booking_id"]) & ~payment_bookings.isin(booking_ids)).sum()),
		"payment_booking_missing": int(_blank_mask(data["raw_payments"]["booking_id"]).sum()),
	}
	_profile_reference_values(data, issues)
	return {"metrics": metrics, "issues": issues}


def _markdown_report(result: dict[str, Any]) -> str:
	metrics = result["metrics"]
	issues = result["issues"]
	lines = [
		"# ASG Airlines RAW Data Profiling Report",
		"",
		"This report was generated by a read-only profile of the MySQL RAW tables. No source values or database rows were changed.",
		"",
		"## Row Counts",
		"",
		"| Table | Rows |",
		"| --- | ---: |",
	]
	for table_name in TABLE_COLUMNS:
		lines.append(f"| `{table_name}` | {metrics[table_name]['row_count']} |")
	lines.extend(["", "## Cross-Table References", "", "| Relationship | Count |", "| --- | ---: |"])
	for name, count in metrics["cross_table_references"].items():
		lines.append(f"| {name.replace('_', ' ')} | {count} |")
	lines.extend(["", "## Key Analysis", "", "```json", json.dumps({key: value for key, value in metrics.items() if key.endswith("_analysis")}, indent=2, default=str), "```", "", "## Discovered Issues", ""])
	if issues:
		lines.extend(["| Issue | Table | Column | Count | Percentage | Examples | Recommended handling |", "| --- | --- | --- | ---: | ---: | --- | --- |"])
		for item in issues:
			lines.append(f"| {item['issue']} | `{item['table']}` | `{item['column']}` | {item['count']} | {item['percentage']}% | {item['examples']} | `{item['recommended_handling']}` |")
	else:
		lines.append("No issues were detected by the configured profiling checks.")
	lines.extend(["", "Recommendations are profiling classifications only. Cleaning, validation, correction, and manual-review actions belong to later phases.", ""])
	return "\n".join(lines)


def main() -> None:
	parser = argparse.ArgumentParser(description="Profile ASG Airlines RAW MySQL tables.")
	parser.add_argument("--report", default="docs/profiling_report.md")
	args = parser.parse_args()
	result = profile_raw_data()
	Path(args.report).write_text(_markdown_report(result), encoding="utf-8")
	print(json.dumps({"row_counts": {table: details["row_count"] for table, details in result["metrics"].items() if table in TABLE_COLUMNS}, "issue_count": len(result["issues"]), "report": args.report}, indent=2))


if __name__ == "__main__":
	main()
