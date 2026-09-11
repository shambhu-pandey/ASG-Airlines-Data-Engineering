# ASG Airlines Data Engineering

## NeoStats Data Engineering Assessment

This project is an end-to-end data engineering pipeline created for the NeoStats ASG Airlines assessment. The project takes airline data from an Excel file, cleans and validates it, stores it in MySQL, creates reporting-ready Gold tables, sends the validated data to Azure MySQL, and uses Power BI to show useful business insights.

**Python | MySQL | Azure MySQL | Power BI | Pytest**

## 1. Project Overview

The source Excel file contains flight, booking, passenger, and payment data with different data quality issues. The pipeline keeps the original data for traceability, cleans records where it is safe to do so, and prepares data for reporting.

```text
Excel
  -> Python
  -> RAW
  -> Cleaning & Validation
  -> CLEAN
  -> Gold
  -> Azure
  -> Power BI
```

- **RAW** keeps source values and ingestion metadata.
- **CLEAN** contains typed records after the project rules are applied.
- **Audit and quarantine** explain corrections and isolate unsafe records.
- **Gold** contains reporting-ready tables.
- **Azure MySQL** receives validated data through incremental synchronization.
- **Power BI** reads Gold tables for business analysis.

## 2. Source Dataset

The source workbook is `data/raw/UseCase - Airlines.xlsx`.

| Sheet | What it contains |
|---|---|
| `flights` | Flight details |
| `bookings` | Booking details |
| `passengers` | Passenger details |
| `payments` | Payment details |

Important flight columns include `flight_id`, `airline`, `source`, `destination`, `departure_time`, `arrival_time`, and duration. Booking, passenger, and payment sheets provide the relationships needed for reporting. The source passenger data contains personal information, but direct passenger identifiers are not exposed in Gold reporting tables.

## 3. Technologies Used

| Technology | Why I used it |
|---|---|
| Python | Data processing and pipeline execution |
| Pandas/OpenPyXL | Reading and processing Excel data |
| MySQL | Data storage for RAW, CLEAN, governance, and Gold layers |
| SQLAlchemy/PyMySQL | Python-MySQL connection |
| Azure MySQL | Cloud database target |
| Power BI | Dashboard and visualization |
| Pytest | Automated testing |
| Git/GitHub | Version control and submission |
| Task Scheduler | Automatic pipeline execution |

## 4. Project Architecture

![Architecture Diagram](images/architecture_diagram.png)

The architecture separates source preservation, cleaning, governance, reporting, cloud synchronization, and visualization. This makes it easier to trace a record from the workbook to the final dashboard.

## 5. Data Flow

![Data Flow Diagram](images/data_flow_diagram.png)

1. Read the Excel workbook.
2. Load source rows into RAW MySQL tables.
3. Profile the incoming data.
4. Clean safe records.
5. Validate keys, relationships, timestamps, and payments.
6. Record corrections in `audit_log`.
7. Move unsafe records to `quarantine_records`.
8. Create Gold reporting tables.
9. Synchronize validated data to Azure.
10. Show the reporting data in Power BI.

## 6. Data Model

![Data Model](images/data_model_diagram.png)

The main Gold tables are:

- `dim_flights`: one row per valid flight.
- `dim_passengers`: passenger reporting information without direct identifiers.
- `booking_payment_summary`: payment count and total payment for each booking.
- `fact_bookings`: the main booking-level reporting table.

`fact_bookings` connects bookings to `dim_flights`, `dim_passengers`, and `booking_payment_summary` using the appropriate business keys.

## 7. Data Ingestion

`src/ingest.py` reads Excel using OpenPyXL and inserts records into RAW tables in batches of 200 rows. The pipeline tracks source row numbers, run IDs, batch IDs, ingestion timestamps, record hashes, and the original row payload.

The source file also receives a SHA-256 hash. If the same source file is run again without any change, the pipeline avoids unnecessary reprocessing.

## 8. Data Cleaning

`src/cleaning.py` handles:

- duplicate records
- conflicting bookings
- invalid IDs
- missing values
- timestamp problems
- flight duration
- airline values
- passenger IDs
- payment values
- duplicate payments

I followed a conservative approach. If I could safely fix a value, I fixed it and recorded the change. If I could not safely determine the correct value, I did not guess it. The record was either kept with a safe NULL/UNKNOWN value or moved to quarantine.

## 9. Data Quality

### Source / RAW

| Dataset | Rows |
|---|---:|
| Flights | 1,020 |
| Bookings | 1,012 |
| Passengers | 1,039 |
| Payments | 1,000 |

### CLEAN

| Dataset | Rows |
|---|---:|
| Flights | 964 |
| Bookings | 1,000 |
| Passengers | 1,000 |
| Payments | 1,000 |

### Audit & Quarantine

| Item | Count |
|---|---:|
| `audit_log` | 78 |
| `quarantine_records` | 107 |

Quarantine categories:

- `flight_id_collision`: 16
- `invalid_flight_time`: 1
- `missing_airline`: 39
- `invalid_or_duplicate_passenger_id`: 39
- `excluded_booking_record`: 12

Quarantine means that a record was kept separately because accepting or correcting it would not be safe. This preserves the record for review without allowing it to affect trusted reporting data.

## 10. Flight Duration

For normal flights, duration is calculated as:

```text
arrival_time - departure_time
```

For overnight flights, the next day's arrival is correctly considered so the duration remains positive. Invalid or over-12-hour records are quarantined.

Verified results:

- Average duration: 164.42 minutes
- Population standard deviation: about 77.33 minutes
- Anomaly range: about 9.75–319.08 minutes
- Duration anomalies: 0
- Anomaly rate: 0%

The source file does not contain scheduled and actual flight times. Because of this, real operational delay cannot be calculated. I have not created fake delay numbers.

Therefore, this project does not report:

- real delay minutes
- delayed-flight count
- on-time percentage

## 11. Payment Logic

- Zero-payment bookings: 363
- Single-payment bookings: 370
- Multiple-payment bookings: 267
- Payment rows in multiple-payment groups: 630
- Orphan payments: 0

Multiple payments can be valid, so they were not incorrectly removed as duplicates. They are combined at booking level in the Gold layer.

Total payment/revenue: **7,982,087.99**

## 12. Audit and Quarantine

`audit_log` records automatic corrections. `quarantine_records` stores records that could not safely be accepted.

This makes it possible to understand:

- what went wrong
- which record was affected
- why it was rejected or corrected
- where it came from

## 13. PII / Security

The original passenger data contains personal information. The Gold reporting layer removes direct identifiers such as:

- names
- passport numbers
- Aadhaar IDs
- email
- phone
- emergency-contact information

Power BI only uses Gold data. Credentials are kept outside Git using environment configuration. This project does not claim that every possible personal-data field has been removed from every processing layer.

## 14. Gold Layer

The Gold layer is created mainly for reporting and Power BI.

| Table | Purpose |
|---|---|
| `dim_flights` | Flight information |
| `dim_passengers` | Passenger reporting information |
| `booking_payment_summary` | Payment summary for each booking |
| `fact_bookings` | Main booking-level reporting table |

Verified counts:

- `dim_flights`: 964
- `dim_passengers`: 1,000
- `booking_payment_summary`: 1,000
- `fact_bookings`: 1,000

There are 42 unmatched booking-flight records. They are retained for traceability.

## 15. Business KPIs

| KPI | Value |
|---|---:|
| Total Bookings | 1,000 |
| Total Passengers | 1,000 |
| Total Revenue | 7,982,087.99 |
| Total Flights | 964 |
| Average Booking Value | approximately 7,982.09 |
| Average Flight Duration | 164.42 minutes |
| Duration Anomaly Rate | 0% |

The dashboard also includes airline and route analysis.

## 16. Power BI Dashboard

### Executive Operations Dashboard

![Executive Dashboard](images/powerbi_executive_dashboard.png)

The page includes Total Bookings, Total Passengers, Total Revenue, Total Flights, Average Booking Value, booking and revenue trends, airline analysis, route analysis, booking status, payment category, passenger age groups, and interactive slicers.

### Flight Operations Analysis

![Flight Operations](images/powerbi_flight_operations.png)

The page includes Average Flight Duration, Total Flights, Validated Flights, Flights by Airline, Average Duration by Airline, route-wise traffic, source/destination analysis, operational slicers, and duration/anomaly insights.

Power BI uses Gold tables only. Direct passenger PII is not exposed. Power BI Desktop refresh is currently manual.

## 17. Azure

I deployed the reporting/data pipeline target to Azure Database for MySQL Flexible Server.

- Resource Group: `rg-neostats-airlines`
- Database: `asg_airlines`
- Region: South India

Validated data is synchronized from local MySQL to Azure. No passwords, usernames, hostnames, or secrets are included in this document.

## 18. Azure Incremental Sync

The synchronization script is `scripts/sync_local_to_azure.py`.

Instead of copying everything again every time, it uses:

- bounded batches
- key-based upserts
- transactions
- schema checks
- Azure identity checks
- duplicate checks
- local/Azure count reconciliation
- deterministic governance event keys

The initial migration scripts are for initial setup or recovery. Normal runs use the incremental synchronization script.

## 19. Automation

The main pipeline is `scripts/run_pipeline.py`.

The seven stages are:

1. Source detection
2. Ingestion
3. Profiling
4. Cleaning
5. Validation
6. Gold transformation
7. Azure synchronization

Windows Task Scheduler is configured for every 30 minutes.

The scheduler trigger was tested separately. A complete seven-stage execution was successfully logged. When the source file does not change, SHA-256 detection correctly skips unnecessary reprocessing. Power BI Desktop refresh remains manual.

## 20. Error Handling and Logging

If one stage fails, later stages are stopped. Logs contain:

- source hash
- stage status
- execution time
- error information

Logs are stored in `logs/pipeline/`. Secrets are not written into logs.

## 21. Testing

## 🧪 Test Result

**20 automated tests passed**

Tests cover:

- data reconciliation
- Gold uniqueness
- payment preservation
- quarantine traceability
- idempotency
- Azure synchronization
- Power BI/PBIR validation
- visual validation

## 22. Scalability

Already implemented:

- batch processing
- layered design
- idempotency
- source-change detection
- bounded Azure writes
- transactions
- reconciliation

### Possible Future Improvements

- Azure Data Factory
- Azure Data Lake
- Databricks/Spark
- monitoring
- alerting
- CI/CD

These are future improvements, not current implementation claims.

## 23. Assumptions

- The supplied Excel workbook is the main source.
- Gold tables are used for reporting.
- Reference mappings are used only when trusted.
- Missing mappings are not invented.
- Unmatched records are retained for traceability.
- The current solution is designed for assessment-scale data.


## 24. Project Structure

```text
ASG-Airlines-Data-Engineering/
├── data/
├── docs/
│   ├── README.md
│   └── images/
├── powerbi/
├── scripts/
├── sql/
├── src/
├── tests/
├── azure/
├── requirements.txt
└── README.md
```
