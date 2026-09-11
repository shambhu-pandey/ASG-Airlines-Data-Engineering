# ASG Airlines Phase 4B Validation Report

**Final status:** `WARNING`

## RAW Invariants

| Table | Actual | Expected | Status |
| --- | ---: | ---: | --- |
| `raw_flights` | 1020 | 1020 | PASS |
| `raw_bookings` | 1012 | 1012 | PASS |
| `raw_passengers` | 1039 | 1039 | PASS |
| `raw_payments` | 1000 | 1000 | PASS |

## CLEAN Counts

| Table | Rows |
| --- | ---: |
| `clean_flights` | 964 |
| `clean_bookings` | 1000 |
| `clean_passengers` | 1000 |
| `clean_payments` | 1000 |

## Key Checks

| Check | Value | Status |
| --- | ---: | --- |
| clean_flights.flight_id missing | 0 | PASS |
| clean_flights.flight_id duplicate groups | 0 | PASS |
| clean_bookings.booking_id missing | 0 | PASS |
| clean_bookings.booking_id duplicate groups | 0 | PASS |
| clean_passengers.passenger_id missing | 0 | PASS |
| clean_passengers.passenger_id duplicate groups | 0 | PASS |
| clean_payments.payment_id missing | 0 | PASS |
| clean_payments.payment_id duplicate groups | 0 | PASS |
| clean_bookings conflicting booking states | 0 | PASS |

## Payment Validation

| Metric | Count |
| --- | ---: |
| total clean payment rows | 1000 |
| bookings with zero payment rows | 363 |
| bookings with one payment row | 370 |
| bookings with multiple payment rows | 267 |
| payment rows belonging to multiple payment bookings | 630 |
| orphan booking references | 0 |
| missing booking id payments | 0 |
| invalid or non numeric amount values | 0 |
| zero amounts | 0 |
| negative amounts | 0 |
| statistical anomalies | 0 |

## Relationships

| Relationship | Matched | Unmatched/orphan | Missing reference |
| --- | ---: | ---: | ---: |
| bookings_to_flights | 958 | 42 | 0 |
| bookings_to_passengers | 1000 | 0 | 0 |
| payments_to_bookings | 1000 | 0 | 0 |

## Flight, Booking, and Passenger Checks

```json
{
  "flights": {
    "missing_flight_id": 0,
    "duplicate_flight_id_groups": 0,
    "missing_airline": 0,
    "invalid_timestamps": 0,
    "arrival_before_departure": 0,
    "duration_over_12_hours": 0,
    "missing_source_or_destination": 0,
    "same_source_destination": 0,
    "ambiguous_prefixes": 4,
    "airport_reference_available": false
  },
  "bookings": {
    "duplicate_booking_id_groups": 0,
    "missing_booking_id": 0,
    "invalid_booking_dates": 0,
    "unexpected_status_values": 75,
    "passport_conflicts": 0,
    "seat_conflicts": 0,
    "emergency_contact_format_candidates": 0
  },
  "passengers": {
    "duplicate_passenger_id_groups": 0,
    "missing_passenger_id": 0,
    "invalid_age": 0,
    "invalid_gender_candidates": 0,
    "invalid_email_candidates": 0,
    "invalid_phone_candidates": 0,
    "invalid_aadhaar_candidates": 0,
    "invalid_date_of_birth": 0
  }
}
```

## Audit Summary

| Entity | Rule | Action | Count |
| --- | --- | --- | ---: |
| payments | PAYMENT_AMOUNT_FLIGHT_MEAN | impute | 46 |
| payments | PAYMENT_AMOUNT_ROUTE_MEAN | impute | 32 |

## Quarantine Summary

| Entity | Category | Count |
| --- | --- | ---: |
| bookings | excluded_booking_record | 12 |
| flights | flight_id_collision | 16 |
| flights | invalid_flight_time | 1 |
| flights | missing_airline | 39 |
| passengers | invalid_or_duplicate_passenger_id | 39 |

Records missing required quarantine traceability fields: 0

## Reconciliation

| Entity | RAW | CLEAN | Quarantine | RAW-CLEAN | CLEAN+Quarantine |
| --- | ---: | ---: | ---: | ---: | ---: |
| flights | 1020 | 964 | 56 | 56 | 1020 |
| bookings | 1012 | 1000 | 12 | 12 | 1012 |
| passengers | 1039 | 1000 | 39 | 39 | 1039 |
| payments | 1000 | 1000 | 0 | 0 | 1000 |

## Known Limitations and Warnings

- The airport reference table is unavailable or empty, so airport validity is reported as a limitation rather than inferred from observed values.
- Airline prefixes remain ambiguous because named airlines and `UNKNOWN` coexist; no airline mapping is invented.
- Clean payment amounts are typed DECIMAL values, so original non-numeric placeholders cannot be distinguished from other NULL amounts at this phase.
- Validation is read-only and does not add foreign keys or change any data.
