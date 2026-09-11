# ASG Airlines Transformation Report

The local reporting layer is derived from the existing CLEAN tables. The transformation does not modify RAW, CLEAN, audit, quarantine, or reference data.

## Reporting Objects

Created by `src/transformation.py` using `sql/06_reporting_tables.sql`:

- `dim_flights`: analytics fields for cleaned flights, including flight date and route.
- `dim_passengers`: age, age group, gender, and date of birth only. Direct PII is excluded.
- `booking_payment_summary`: one row per booking with payment count, total amount, method summary, and payment category.
- `fact_bookings`: one row per cleaned booking joined to passenger, flight, and payment summary data.

## Transformations

- Flight route is derived as `source->destination`.
- Flight date is derived from departure time.
- Booking year, month, month name, and day are derived from booking date.
- Passenger age groups are `UNDER_18`, `18_35`, `36_55`, `56_PLUS`, or `UNKNOWN`.
- Payment categories are `UNPAID`, `SINGLE_PAYMENT`, and `MULTIPLE_PAYMENTS`.
- Payments are aggregated by `booking_id`; repeated payment rows are not deduplicated.
- Booking facts use a left join to flights. Missing flight matches remain in the fact table with NULL flight details and `flight_match_status = 'UNMATCHED_FLIGHT'`.

## PII Protection

Reporting tables exclude passenger names, email, phone, Aadhaar, passport number, and emergency contact fields. No direct PII is required for the reporting KPIs.

## Verification Results

| Object | Rows |
| --- | ---: |
| `dim_flights` | 964 |
| `dim_passengers` | 1,000 |
| `booking_payment_summary` | 1,000 |
| `fact_bookings` | 1,000 |

- CLEAN payment total: `7,982,087.99`.
- Reporting payment total: `7,982,087.99`.
- Multiple-payment booking groups preserved: `267`.
- Unpaid bookings represented: `363`.
- Unmatched booking-flight facts: `42`.
- Duplicate reporting booking IDs: `0`.
- Duplicate flight keys: `0`.
- Duplicate passenger keys: `0`.
- Duplicate booking-payment summary keys: `0`.
- Multiple-payment groups in CLEAN and Gold: `267` and `267`.
- Gold payment total reconciles to CLEAN: `7,982,087.99`.

The transformation is repeatable: it rebuilds only the derived reporting tables in one transaction. RAW and CLEAN tables remain unchanged.
