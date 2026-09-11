USE asg_airlines;

CREATE TABLE IF NOT EXISTS airline_prefix_reference (
    airline_prefix VARCHAR(16) NOT NULL,
    airline_name VARCHAR(128) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from DATETIME(6) NULL,
    effective_to DATETIME(6) NULL,
    PRIMARY KEY (airline_prefix),
    KEY idx_airline_prefix_active (active)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS airport_reference (
    iata_code CHAR(3) NOT NULL,
    airport_name VARCHAR(255) NOT NULL,
    city VARCHAR(128) NULL,
    country VARCHAR(128) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (iata_code),
    KEY idx_airport_reference_active (active)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS payment_method_reference (
    payment_method VARCHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (payment_method)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS pipeline_config (
    rule_name VARCHAR(128) NOT NULL,
    rule_version VARCHAR(32) NOT NULL DEFAULT '1',
    rule_value TEXT NOT NULL,
    description VARCHAR(512) NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from DATETIME(6) NULL,
    effective_to DATETIME(6) NULL,
    PRIMARY KEY (rule_name, rule_version),
    KEY idx_pipeline_config_active (active),
    KEY idx_pipeline_config_name_active (rule_name, active)
) ENGINE = InnoDB;
