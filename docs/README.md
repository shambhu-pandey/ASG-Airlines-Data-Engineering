# Operating Architecture

```text
Source Excel
	|
	v
Pipeline Orchestrator
	|
	v
RAW
	|
	v
CLEAN
	|
	v
Validation / Quarantine
	|
	v
GOLD
	|
	v
Azure MySQL
	|
	v
Power BI
```

## Implemented Automatically

- Source existence checks and SHA-256 change detection.
- Existing ingestion, profiling, cleaning, validation, and Gold transformation modules.
- Failure propagation, timestamped logs, and successful-source state.
- Incremental Azure core and Gold synchronization with bounded, key-based upserts, optional read-only preflight checks, and local-vs-Azure count reconciliation.
- Windows watch mode and a per-user Task Scheduler wrapper.

## Manually Triggered

Power BI Desktop refresh remains manual. After the Gold tables are loaded, open the PBIP report and use the Power BI Desktop **Refresh** action. The project does not claim automatic Power BI refresh. The original empty-target migration utilities remain for initial provisioning or recovery and are not used by the scheduled orchestrator.

## Future Production Enhancements

Azure Data Factory or another managed scheduler, centralized secret management, monitoring and alerting, CI/CD, and a tested Power BI Service REST refresh can be added later without changing the current business rules or stage implementations.
