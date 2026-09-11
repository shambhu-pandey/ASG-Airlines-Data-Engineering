# Cleaned Visualization Exports

These CSVs are generated from the validated local CLEAN/Gold reporting layer for assessment visualization and review. They do not replace the MySQL source of truth.

| File | Grain | Notes |
| --- | --- | --- |
| `clean_flights.csv` | One row per cleaned flight | Flight fields and validated duration only. |
| `clean_bookings.csv` | One row per cleaned booking | Direct passport and emergency-contact fields excluded. |
| `clean_passengers.csv` | One row per reporting passenger | Uses the PII-reduced Gold passenger dimension. |
| `clean_payments.csv` | One row per cleaned payment | Payment identifiers and amounts; no passenger PII. |

The exports contain no direct passenger names, passport numbers, Aadhaar IDs, email addresses, phone numbers, or emergency-contact fields.
