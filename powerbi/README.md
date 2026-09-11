Power BI should connect to the local reporting tables created from CLEAN data:

- `dim_flights`
- `dim_passengers`
- `booking_payment_summary`
- `fact_bookings`

These tables exclude direct passenger PII and expose unmatched booking-flight references through `flight_match_status`.
Dashboard and semantic-model files will be added in a later phase.
