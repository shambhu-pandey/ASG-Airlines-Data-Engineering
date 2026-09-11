CREATE DATABASE IF NOT EXISTS asg_airlines
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE asg_airlines;

CREATE TABLE IF NOT EXISTS etl_runs (
    run_id VARCHAR(64) NOT NULL,
    source_file VARCHAR(512) NOT NULL,
    file_hash CHAR(64) NULL,
    started_at DATETIME(6) NOT NULL,
    ended_at DATETIME(6) NULL,
    status VARCHAR(32) NOT NULL,
    error_message TEXT NULL,
    notes TEXT NULL,
    PRIMARY KEY (run_id),
    KEY idx_etl_runs_status (status),
    KEY idx_etl_runs_started_at (started_at)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS etl_batches (
    batch_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    table_name VARCHAR(128) NOT NULL,
    batch_number INT UNSIGNED NOT NULL,
    source_row_start BIGINT UNSIGNED NULL,
    source_row_end BIGINT UNSIGNED NULL,
    row_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL,
    started_at DATETIME(6) NOT NULL,
    ended_at DATETIME(6) NULL,
    error_message TEXT NULL,
    PRIMARY KEY (batch_id),
    UNIQUE KEY uq_etl_batches_run_number (run_id, batch_number),
    KEY idx_etl_batches_run_id (run_id),
    KEY idx_etl_batches_table_status (table_name, status),
    CONSTRAINT fk_etl_batches_run
        FOREIGN KEY (run_id) REFERENCES etl_runs (run_id)
) ENGINE = InnoDB;
