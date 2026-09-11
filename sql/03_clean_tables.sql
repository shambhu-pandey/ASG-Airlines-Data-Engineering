USE asg_airlines;

CREATE TABLE IF NOT EXISTS clean_flights (
    clean_flight_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    source_file VARCHAR(512) NOT NULL,
    source_row_num BIGINT UNSIGNED NOT NULL,
    processed_at DATETIME(6) NOT NULL,
    flight_id VARCHAR(64) NOT NULL,
    airline VARCHAR(128) NOT NULL,
    source CHAR(3) NOT NULL,
    destination CHAR(3) NOT NULL,
    departure_time DATETIME(6) NULL,
    arrival_time DATETIME(6) NULL,
    duration_minutes DECIMAL(8, 2) NULL,
    PRIMARY KEY (clean_flight_id),
    UNIQUE KEY uq_clean_flights_flight_id (flight_id),
    KEY idx_clean_flights_route (source, destination),
    KEY idx_clean_flights_batch (batch_id)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS clean_bookings (
    clean_booking_row_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    source_file VARCHAR(512) NOT NULL,
    source_row_num BIGINT UNSIGNED NOT NULL,
    processed_at DATETIME(6) NOT NULL,
    booking_id VARCHAR(64) NOT NULL,
    passenger_id VARCHAR(64) NULL,
    flight_id VARCHAR(64) NULL,
    booking_date DATE NULL,
    status VARCHAR(32) NULL,
    passport_number VARCHAR(64) NULL,
    seat_number VARCHAR(16) NULL,
    emergency_contact_name VARCHAR(255) NULL,
    emergency_contact_phone VARCHAR(64) NULL,
    PRIMARY KEY (clean_booking_row_id),
    UNIQUE KEY uq_clean_bookings_booking_id (booking_id),
    KEY idx_clean_bookings_passenger (passenger_id),
    KEY idx_clean_bookings_flight (flight_id),
    KEY idx_clean_bookings_booking_date (booking_date),
    KEY idx_clean_bookings_batch (batch_id)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS clean_passengers (
    clean_passenger_row_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    source_file VARCHAR(512) NOT NULL,
    source_row_num BIGINT UNSIGNED NOT NULL,
    processed_at DATETIME(6) NOT NULL,
    passenger_id VARCHAR(64) NOT NULL,
    first_name VARCHAR(128) NULL,
    last_name VARCHAR(128) NULL,
    age SMALLINT UNSIGNED NULL,
    gender VARCHAR(32) NULL,
    email VARCHAR(320) NULL,
    phone VARCHAR(64) NULL,
    aadhaar_id VARCHAR(64) NULL,
    date_of_birth DATE NULL,
    PRIMARY KEY (clean_passenger_row_id),
    UNIQUE KEY uq_clean_passengers_passenger_id (passenger_id),
    KEY idx_clean_passengers_email (email),
    KEY idx_clean_passengers_batch (batch_id)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS clean_payments (
    payment_id VARCHAR(128) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    source_file VARCHAR(512) NOT NULL,
    source_row_num BIGINT UNSIGNED NOT NULL,
    processed_at DATETIME(6) NOT NULL,
    booking_id VARCHAR(64) NULL,
    amount DECIMAL(12, 2) NULL,
    payment_method VARCHAR(64) NULL,
    source_key_missing BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (payment_id),
    KEY idx_clean_payments_booking (booking_id),
    KEY idx_clean_payments_batch (batch_id),
    KEY idx_clean_payments_method (payment_method)
) ENGINE = InnoDB;
