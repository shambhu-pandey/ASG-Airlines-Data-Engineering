"""Batch RAW-to-CLEAN processing for the ASG Airlines assessment."""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from .db import get_engine


TABLES = {
	"raw_flights": "clean_flights",
	"raw_bookings": "clean_bookings",
	"raw_passengers": "clean_passengers",
	"raw_payments": "clean_payments",
}


def _blank(value: Any) -> bool:
	return pd.isna(value) or str(value).strip() == ""


def _clean_text(value: Any) -> str | None:
	return None if _blank(value) else str(value).strip()


def _now() -> datetime:
	return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit(connection: Any, row: pd.Series, entity: str, record_id: Any, rule: str, action: str, before: Any, after: Any, reason: str, confidence: float | None = None) -> None:
	connection.execute(text("""
		INSERT INTO audit_log
		(run_id, batch_id, entity_name, record_identifier, rule_identifier,
		 action, before_value, after_value, reason, confidence, event_timestamp)
		VALUES (:run_id, :batch_id, :entity, :record_id, :rule, :action,
				:before, :after, :reason, :confidence, :event_timestamp)
	"""), {
		"run_id": row["run_id"], "batch_id": row["batch_id"], "entity": entity,
		"record_id": None if _blank(record_id) else str(record_id), "rule": rule,
		"action": action, "before": None if before is None else str(before),
		"after": None if after is None else str(after), "reason": reason,
		"confidence": confidence, "event_timestamp": _now(),
	})


def _quarantine(connection: Any, row: pd.Series, entity: str, record_id: Any, category: str, reason: str, severity: str = "error") -> None:
	connection.execute(text("""
		INSERT INTO quarantine_records
		(run_id, batch_id, entity_name, source_row_num, record_identifier,
		 issue_category, reason, severity, original_payload, trace_details, created_at)
		VALUES (:run_id, :batch_id, :entity, :source_row_num, :record_id,
				:category, :reason, :severity, :payload, :trace_details, :created_at)
	"""), {
		"run_id": row["run_id"], "batch_id": row["batch_id"], "entity": entity,
		"source_row_num": int(row["source_row_num"]),
		"record_id": None if _blank(record_id) else str(record_id), "category": category,
		"reason": reason, "severity": severity, "payload": row.get("raw_payload"),
		"trace_details": f"source_file={row['source_file']}", "created_at": _now(),
	})


def _read(connection: Any, table: str, columns: list[str]) -> pd.DataFrame:
	return pd.read_sql_query(text(f"SELECT {', '.join(columns)} FROM {table} ORDER BY source_row_num"), connection)


def _insert_rows(connection: Any, table: str, rows: list[dict[str, Any]]) -> None:
	if not rows:
		return
	rows = [
		{
			column: None if value is pd.NA or value is pd.NaT or (isinstance(value, float) and math.isnan(value)) else value
			for column, value in row.items()
		}
		for row in rows
	]
	columns = list(rows[0])
	connection.execute(
		text(f"INSERT INTO {table} ({', '.join(f'`{column}`' for column in columns)}) VALUES ({', '.join(f':{column}' for column in columns)})"),
		rows,
	)


def _base(row: pd.Series, processed_at: datetime) -> dict[str, Any]:
	return {"run_id": row["run_id"], "batch_id": row["batch_id"], "source_file": row["source_file"], "source_row_num": int(row["source_row_num"]), "processed_at": processed_at}


def _clean_flights(connection: Any, frame: pd.DataFrame, processed_at: datetime) -> tuple[list[dict[str, Any]], int, int]:
	rows, quarantined, corrections, seen = [], 0, 0, set()
	prefixes = pd.read_sql_query(text("SELECT airline_prefix, airline_name FROM airline_prefix_reference WHERE active = TRUE"), connection)
	prefix_map = dict(zip(prefixes["airline_prefix"].astype(str).str.upper(), prefixes["airline_name"].astype(str)))
	for _, source in frame.iterrows():
		flight_id = _clean_text(source["flight_id"])
		if not flight_id or flight_id in seen:
			_quarantine(connection, source, "flights", flight_id, "flight_id_collision" if flight_id else "invalid_critical_identifier", "later collision record retained in RAW; first ingestion record selected" if flight_id else "flight_id is missing", "review")
			quarantined += 1
			continue
		seen.add(flight_id)
		airline = _clean_text(source["airline"])
		if not airline and flight_id[:2].upper() in prefix_map:
			airline = prefix_map[flight_id[:2].upper()]
			_audit(connection, source, "flights", flight_id, "FLIGHT_AIRLINE_PREFIX", "impute", None, airline, "derived from active airline prefix reference", 0.95)
			corrections += 1
		if not airline:
			_quarantine(connection, source, "flights", flight_id, "missing_airline", "airline missing and prefix is not in reference")
			quarantined += 1
			continue
		departure = pd.to_datetime(source["departure_time"], errors="coerce")
		arrival = pd.to_datetime(source["arrival_time"], errors="coerce")
		duration = (arrival - departure).total_seconds() / 60 if pd.notna(departure) and pd.notna(arrival) and arrival >= departure else None
		if duration is None or duration > 720:
			_quarantine(connection, source, "flights", flight_id, "invalid_flight_time", "valid non-negative duration could not be derived within 12-hour cap", "review")
			quarantined += 1
			continue
		source_airport, destination = _clean_text(source["source"]), _clean_text(source["destination"])
		if not source_airport or not destination:
			_quarantine(connection, source, "flights", flight_id, "missing_airport", "source or destination is missing")
			quarantined += 1
			continue
		rows.append({**_base(source, processed_at), "flight_id": flight_id, "airline": airline, "source": source_airport.upper(), "destination": destination.upper(), "departure_time": departure.to_pydatetime(), "arrival_time": arrival.to_pydatetime(), "duration_minutes": duration})
	return rows, quarantined, corrections


def _clean_passengers(connection: Any, frame: pd.DataFrame, processed_at: datetime) -> tuple[list[dict[str, Any]], int, int]:
	rows, quarantined, corrections, seen = [], 0, 0, set()
	for _, source in frame.iterrows():
		passenger_id = _clean_text(source["passenger_id"])
		if not passenger_id or passenger_id in seen:
			_quarantine(connection, source, "passengers", passenger_id, "invalid_or_duplicate_passenger_id", "passenger_id is missing or duplicated")
			quarantined += 1
			continue
		seen.add(passenger_id)
		age = pd.to_numeric(source["age"], errors="coerce")
		if pd.isna(age) or age < 0 or age > 120:
			_quarantine(connection, source, "passengers", passenger_id, "invalid_age", "age is missing or outside the supported range")
			quarantined += 1
			continue
		dob = pd.to_datetime(source["date_of_birth"], errors="coerce")
		if not _blank(source["date_of_birth"]) and pd.isna(dob):
			_quarantine(connection, source, "passengers", passenger_id, "invalid_date_of_birth", "date_of_birth is not parseable")
			quarantined += 1
			continue
		values = {column: _clean_text(source[column]) for column in ["first_name", "last_name", "gender", "email", "phone", "aadhaar_id"]}
		if values["email"] and values["email"] != values["email"].lower():
			_audit(connection, source, "passengers", passenger_id, "PASSENGER_EMAIL_CASE", "normalize", values["email"], values["email"].lower(), "email normalized to lowercase")
			values["email"], corrections = values["email"].lower(), corrections + 1
		rows.append({**_base(source, processed_at), "passenger_id": passenger_id, **values, "age": int(age), "date_of_birth": dob.date() if pd.notna(dob) else None})
	return rows, quarantined, corrections


def _clean_bookings(connection: Any, frame: pd.DataFrame, payments: pd.DataFrame, processed_at: datetime) -> tuple[list[dict[str, Any]], int, int]:
	rows, quarantined, corrections = [], 0, 0
	frame = frame.copy()
	source_columns = ["booking_id", "passenger_id", "flight_id", "booking_date", "status", "passport_number", "seat_number", "emergency_contact_name", "emergency_contact_phone"]
	frame["_blank_row"] = frame[source_columns].apply(lambda row: all(_blank(value) for value in row), axis=1)
	frame["_exact_duplicate"] = frame.duplicated(subset=source_columns, keep="first")
	payment_booking_ids = set(payments["booking_id"].dropna().astype(str).str.strip())
	prepared = []
	for _, source in frame.iterrows():
		booking_id = _clean_text(source["booking_id"])
		if source["_blank_row"] or source["_exact_duplicate"]:
			_quarantine(connection, source, "bookings", booking_id, "excluded_booking_record", "completely blank or exact duplicate booking row", "info")
			quarantined += 1
			continue
		booking_date = pd.to_datetime(source["booking_date"], errors="coerce")
		if not booking_id or pd.isna(booking_date):
			_quarantine(connection, source, "bookings", booking_id, "invalid_booking_record", "booking_id or booking_date is missing/unparseable")
			quarantined += 1
			continue
		normalized = source.copy()
		normalized["booking_id"], normalized["booking_date"] = booking_id, booking_date.date()
		normalized["status"] = _clean_text(source["status"])
		for column in ["passenger_id", "flight_id", "passport_number", "seat_number", "emergency_contact_name", "emergency_contact_phone"]:
			normalized[column] = _clean_text(source[column])
		if normalized["passport_number"]:
			upper = normalized["passport_number"].upper()
			if upper != normalized["passport_number"]:
				_audit(connection, source, "bookings", booking_id, "BOOKING_PASSPORT_CASE", "normalize", normalized["passport_number"], upper, "passport normalized to uppercase")
				corrections += 1
			normalized["passport_number"] = upper
		prepared.append(normalized)
	prepared_frame = pd.DataFrame(prepared)
	if prepared_frame.empty:
		return rows, quarantined, corrections
	for booking_id, group in prepared_frame.groupby("booking_id", sort=False):
		selected = group.sort_values(["booking_date", "source_row_num"]).iloc[-1].copy()
		if len(group) > 1:
			same_date = group[group["booking_date"] == selected["booking_date"]]
			if booking_id in payment_booking_ids:
				preferred = same_date[same_date["status"].astype("string").str.upper().isin(["CONFIRMED", "CANCELLED"])]
				if not preferred.empty:
					selected = preferred.iloc[-1].copy()
			cancelled = same_date[same_date["status"].astype("string").str.upper() == "CANCELLED"]
			if not cancelled.empty:
				selected = cancelled.iloc[-1].copy()
			for _, discarded in group.iterrows():
				if int(discarded["source_row_num"]) != int(selected["source_row_num"]):
					_quarantine(connection, discarded, "bookings", booking_id, "booking_id_conflict", "conflicting duplicate; selected latest/status-preferred record", "review")
					quarantined += 1
			_audit(connection, selected, "bookings", booking_id, "BOOKING_CONFLICT_SELECTION", "select", len(group), selected["source_row_num"], "duplicate booking conflict resolved by locked selection rules")
		rows.append({**_base(selected, processed_at), "booking_id": selected["booking_id"], "passenger_id": selected["passenger_id"], "flight_id": selected["flight_id"], "booking_date": selected["booking_date"], "status": selected["status"], "passport_number": selected["passport_number"], "seat_number": selected["seat_number"], "emergency_contact_name": selected["emergency_contact_name"], "emergency_contact_phone": selected["emergency_contact_phone"]})
	return rows, quarantined, corrections


def _clean_payments(connection: Any, frame: pd.DataFrame, bookings: pd.DataFrame, flights: pd.DataFrame, processed_at: datetime) -> tuple[list[dict[str, Any]], int, int]:
	rows, quarantined, corrections, seen = [], 0, 0, set()
	booking_to_flight = dict(zip(bookings["booking_id"].astype(str).str.strip(), bookings["flight_id"].astype(str).str.strip()))
	working = frame.assign(_amount=pd.to_numeric(frame["amount"], errors="coerce"), _flight_id=frame["booking_id"].astype("string").str.strip().map(booking_to_flight))
	flight_means = working.groupby("_flight_id")["_amount"].mean()
	route_map = flights.assign(_flight_id=flights["flight_id"].astype(str).str.strip()).set_index("_flight_id").apply(lambda row: f"{row['source']}->{row['destination']}", axis=1).to_dict()
	route_means = working.assign(_route=working["_flight_id"].map(route_map)).groupby("_route")["_amount"].mean()
	global_mean = working["_amount"].mean()
	method_mode = frame["payment_method"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().mode()
	batch_mode = method_mode.iloc[0] if not method_mode.empty else None
	for _, source in frame.iterrows():
		booking_id, payment_id = _clean_text(source["booking_id"]), _clean_text(source["payment_id"])
		if not booking_id:
			_quarantine(connection, source, "payments", payment_id, "unlinked_payment", "booking_id is missing; payment is excluded from passenger/flight reporting", "review")
			quarantined += 1
			continue
		if payment_id and payment_id in seen:
			_quarantine(connection, source, "payments", payment_id, "duplicate_payment_id", "duplicate payment_id rejected; existing payment retained")
			quarantined += 1
			continue
		amount = pd.to_numeric(source["amount"], errors="coerce")
		if pd.isna(amount):
			flight_id = booking_to_flight.get(booking_id)
			amount = flight_means.get(flight_id)
			rule = "PAYMENT_AMOUNT_FLIGHT_MEAN"
			if pd.isna(amount):
				amount = route_means.get(route_map.get(flight_id))
				rule = "PAYMENT_AMOUNT_ROUTE_MEAN"
			if pd.isna(amount):
				amount, rule = global_mean, "PAYMENT_AMOUNT_GLOBAL_MEAN"
			if pd.notna(amount):
				_audit(connection, source, "payments", payment_id, rule, "impute", source["amount"], amount, "non-numeric amount replaced using statistical fallback")
				corrections += 1
		if not payment_id:
			raw_key = f"{booking_id}|{source['amount']}|{source['payment_method']}|{source['batch_id']}"
			payment_id = hashlib.sha256(raw_key.encode()).hexdigest()
			_audit(connection, source, "payments", payment_id, "PAYMENT_SURROGATE_KEY", "generate", None, payment_id, "missing payment_id replaced with deterministic batch-scoped hash")
			corrections += 1
		seen.add(payment_id)
		method = _clean_text(source["payment_method"])
		if not method and batch_mode:
			_audit(connection, source, "payments", payment_id, "PAYMENT_METHOD_BATCH_MODE", "impute", None, batch_mode, "missing payment method filled with current batch mode")
			method, corrections = batch_mode, corrections + 1
		rows.append({**_base(source, processed_at), "payment_id": payment_id, "booking_id": booking_id, "amount": None if pd.isna(amount) else float(amount), "payment_method": method, "source_key_missing": not bool(_clean_text(source["payment_id"]))})
	return rows, quarantined, corrections


def clean_latest_run() -> dict[str, Any]:
	"""Clean the latest successful ingestion run in one transaction."""
	with get_engine().begin() as connection:
		run_id = connection.execute(text("SELECT run_id FROM etl_runs WHERE status = 'succeeded' ORDER BY started_at DESC LIMIT 1")).scalar_one_or_none()
		if not run_id:
			raise RuntimeError("No successful ingestion run is available for cleaning.")
		for table in TABLES.values():
			connection.execute(text(f"DELETE FROM {table} WHERE run_id = :run_id"), {"run_id": run_id})
		connection.execute(text("DELETE FROM audit_log WHERE run_id = :run_id"), {"run_id": run_id})
		connection.execute(text("DELETE FROM quarantine_records WHERE run_id = :run_id"), {"run_id": run_id})
		columns = "run_id, batch_id, source_file, source_row_num, flight_id, airline, source, destination, departure_time, arrival_time, duration, raw_payload"
		flights = _read(connection, "raw_flights", columns.split(", "))
		bookings = _read(connection, "raw_bookings", "run_id, batch_id, source_file, source_row_num, booking_id, passenger_id, flight_id, booking_date, status, passport_number, seat_number, emergency_contact_name, emergency_contact_phone, raw_payload".split(", "))
		passengers = _read(connection, "raw_passengers", "run_id, batch_id, source_file, source_row_num, passenger_id, first_name, last_name, age, gender, email, phone, aadhaar_id, date_of_birth, raw_payload".split(", "))
		payments = _read(connection, "raw_payments", "run_id, batch_id, source_file, source_row_num, payment_id, booking_id, amount, payment_method, raw_payload".split(", "))
		processed_at = _now()
		clean_flights, flight_q, flight_a = _clean_flights(connection, flights, processed_at)
		clean_passengers, passenger_q, passenger_a = _clean_passengers(connection, passengers, processed_at)
		clean_bookings, booking_q, booking_a = _clean_bookings(connection, bookings, payments, processed_at)
		booking_frame = pd.DataFrame(clean_bookings) if clean_bookings else bookings[["booking_id", "flight_id"]]
		clean_payments, payment_q, payment_a = _clean_payments(connection, payments, booking_frame, flights, processed_at)
		_insert_rows(connection, "clean_flights", clean_flights)
		_insert_rows(connection, "clean_passengers", clean_passengers)
		_insert_rows(connection, "clean_bookings", clean_bookings)
		_insert_rows(connection, "clean_payments", clean_payments)
		return {"run_id": run_id, "clean_counts": {"flights": len(clean_flights), "bookings": len(clean_bookings), "passengers": len(clean_passengers), "payments": len(clean_payments)}, "quarantined_count": flight_q + passenger_q + booking_q + payment_q, "audit_correction_count": flight_a + passenger_a + booking_a + payment_a}


if __name__ == "__main__":
	argparse.ArgumentParser(description="Clean the latest ASG Airlines RAW ingestion run.").parse_args()
	print(json.dumps(clean_latest_run(), indent=2, default=str))
