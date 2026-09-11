USE asg_airlines;

-- Grain: one row per valid clean flight.
CREATE TABLE IF NOT EXISTS dim_flights (
    flight_id VARCHAR(64) NOT NULL,
    airline VARCHAR(128) NOT NULL,
    source CHAR(3) NOT NULL,
    destination CHAR(3) NOT NULL,
    departure_time DATETIME(6) NULL,
    arrival_time DATETIME(6) NULL,
    duration_minutes DECIMAL(8, 2) NULL,
    duration_anomaly_flag TINYINT(1) NOT NULL DEFAULT 0,
    duration_anomaly_reason VARCHAR(64) NULL,
    flight_date DATE NULL,
    route VARCHAR(16) NOT NULL,
    PRIMARY KEY (flight_id),
    KEY idx_dim_flights_route (route),
    KEY idx_dim_flights_date (flight_date)
) ENGINE = InnoDB;

-- Grain: one row per valid clean passenger; direct PII is intentionally excluded.
CREATE TABLE IF NOT EXISTS dim_passengers (
    passenger_id VARCHAR(64) NOT NULL,
    age SMALLINT UNSIGNED NULL,
    age_group VARCHAR(32) NULL,
    gender VARCHAR(32) NULL,
    date_of_birth DATE NULL,
    PRIMARY KEY (passenger_id),
    KEY idx_dim_passengers_age_group (age_group),
    KEY idx_dim_passengers_gender (gender)
) ENGINE = InnoDB;

-- Grain: one row per booking, aggregating zero, one, or multiple clean payments.
CREATE TABLE IF NOT EXISTS booking_payment_summary (
    booking_id VARCHAR(64) NOT NULL,
    payment_count INT UNSIGNED NOT NULL DEFAULT 0,
    total_payment_amount DECIMAL(14, 2) NOT NULL DEFAULT 0,
    payment_method_summary VARCHAR(512) NULL,
    payment_category VARCHAR(32) NOT NULL,
    PRIMARY KEY (booking_id),
    KEY idx_booking_payment_category (payment_category)
) ENGINE = InnoDB;

-- Grain: one row per clean booking; payment rows are joined only through the booking aggregate.
CREATE TABLE IF NOT EXISTS fact_bookings (
    booking_id VARCHAR(64) NOT NULL,
    passenger_id VARCHAR(64) NULL,
    flight_id VARCHAR(64) NULL,
    airline VARCHAR(128) NULL,
    source CHAR(3) NULL,
    destination CHAR(3) NULL,
    booking_date DATE NULL,
    booking_year SMALLINT NULL,
    booking_month TINYINT UNSIGNED NULL,
    booking_month_name VARCHAR(16) NULL,
    booking_day TINYINT UNSIGNED NULL,
    status VARCHAR(32) NULL,
    seat_number VARCHAR(16) NULL,
    departure_time DATETIME(6) NULL,
    arrival_time DATETIME(6) NULL,
    duration_minutes DECIMAL(8, 2) NULL,
    flight_date DATE NULL,
    route VARCHAR(16) NULL,
    passenger_age_group VARCHAR(32) NULL,
    payment_count INT UNSIGNED NOT NULL DEFAULT 0,
    total_payment_amount DECIMAL(14, 2) NOT NULL DEFAULT 0,
    payment_method_summary VARCHAR(512) NULL,
    payment_category VARCHAR(32) NOT NULL,
    flight_match_status VARCHAR(32) NOT NULL,
    PRIMARY KEY (booking_id),
    KEY idx_fact_bookings_date (booking_date),
    KEY idx_fact_bookings_flight (flight_id),
    KEY idx_fact_bookings_passenger (passenger_id),
    KEY idx_fact_bookings_status (status),
    KEY idx_fact_bookings_match (flight_match_status)
) ENGINE = InnoDB;
