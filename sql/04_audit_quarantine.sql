USE asg_airlines;

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NULL,
    entity_name VARCHAR(128) NOT NULL,
    record_identifier VARCHAR(255) NULL,
    rule_identifier VARCHAR(128) NULL,
    action VARCHAR(64) NOT NULL,
    before_value LONGTEXT NULL,
    after_value LONGTEXT NULL,
    reason TEXT NULL,
    confidence DECIMAL(5, 4) NULL,
    event_timestamp DATETIME(6) NOT NULL,
    PRIMARY KEY (audit_id),
    KEY idx_audit_log_run_batch (run_id, batch_id),
    KEY idx_audit_log_entity_record (entity_name, record_identifier),
    KEY idx_audit_log_rule (rule_identifier),
    CONSTRAINT chk_audit_confidence
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS quarantine_records (
    quarantine_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NULL,
    entity_name VARCHAR(128) NOT NULL,
    source_row_num BIGINT UNSIGNED NULL,
    record_identifier VARCHAR(255) NULL,
    issue_category VARCHAR(128) NOT NULL,
    reason TEXT NOT NULL,
    severity VARCHAR(32) NOT NULL,
    original_payload JSON NULL,
    trace_details TEXT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'quarantined',
    created_at DATETIME(6) NOT NULL,
    reviewed_at DATETIME(6) NULL,
    review_decision VARCHAR(255) NULL,
    PRIMARY KEY (quarantine_id),
    KEY idx_quarantine_run_batch (run_id, batch_id),
    KEY idx_quarantine_entity_status (entity_name, status),
    KEY idx_quarantine_record (record_identifier),
    KEY idx_quarantine_created_at (created_at)
) ENGINE = InnoDB;
