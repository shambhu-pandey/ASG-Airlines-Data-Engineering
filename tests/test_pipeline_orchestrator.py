from __future__ import annotations

import hashlib
from argparse import Namespace
from pathlib import Path
import subprocess

import scripts.run_pipeline as pipeline


def make_args(source: Path, **overrides: object) -> Namespace:
    values = {
        "file": str(source),
        "skip_azure": True,
        "skip_profiling": False,
        "preflight_azure": False,
        "dry_run": False,
        "watch": False,
        "poll_interval": 1.0,
    }
    values.update(overrides)
    return Namespace(**values)


def test_source_exists_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source-content")
    expected = hashlib.sha256(b"source-content").hexdigest()
    assert source.is_file()
    assert pipeline.source_sha256(source) == expected


def test_unchanged_and_changed_source_detection(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"one")
    state = tmp_path / "state.json"
    digest = pipeline.source_sha256(source)
    pipeline.write_success_state(source, digest, state)
    assert pipeline.source_is_unchanged(source, digest, state)
    source.write_bytes(b"two")
    assert not pipeline.source_is_unchanged(source, pipeline.source_sha256(source), state)


def test_local_only_success_does_not_satisfy_azure_run(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"one")
    state = tmp_path / "state.json"
    pipeline.write_success_state(source, pipeline.source_sha256(source), state, "SKIPPED")
    saved = pipeline.load_state(state)
    assert saved["status"] == "SUCCESS"
    assert saved["azure_migration"] == "SKIPPED"
    assert not (saved.get("azure_migration") == "COMPLETED")


def test_dry_run_does_not_execute_or_write_success_state(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"dry-run")
    state = tmp_path / "state.json"
    logs = tmp_path / "logs"
    monkeypatch.setattr(pipeline, "STATE_PATH", state)
    monkeypatch.setattr(pipeline, "LOG_DIR", logs)
    called = []

    def runner(*args, **kwargs):
        called.append(args)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    result = pipeline.run_once(make_args(source, dry_run=True), runner=runner)
    assert result == 0
    assert called == []
    assert not state.exists()


def test_command_construction(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    stages = pipeline.build_stages(source, skip_azure=True, skip_profiling=False, preflight_azure=False)
    assert [stage.name for stage in stages] == ["INGESTION", "PROFILING", "CLEANING", "VALIDATION", "GOLD TRANSFORMATION"]
    assert stages[0].command[1:3] == ("-m", "src.ingest")
    assert str(source) in stages[0].command


def test_azure_stages_use_incremental_sync(tmp_path: Path) -> None:
    stages = pipeline.build_stages(tmp_path / "source.xlsx", skip_azure=False, skip_profiling=False, preflight_azure=False)
    assert [stage.name for stage in stages[-2:]] == ["AZURE CORE SYNC", "AZURE GOLD SYNC"]
    assert all(any(item.endswith("sync_local_to_azure.py") for item in stage.command) for stage in stages[-2:])
    assert all(not any(item.endswith("migrate_local_to_azure.py") for item in stage.command) for stage in stages)
    assert all(not any(item.endswith("migrate_gold_to_azure.py") for item in stage.command) for stage in stages)


def test_failure_propagates_and_stops_subsequent_stages(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"failure")
    monkeypatch.setattr(pipeline, "LOG_DIR", tmp_path / "logs")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 7, "stage output", "stage error")

    result = pipeline.run_once(make_args(source), runner=runner)
    assert result != 0
    assert len(calls) == 1


def test_success_updates_state_and_redacts_secrets(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"success")
    state = tmp_path / "state.json"
    logs = tmp_path / "logs"
    monkeypatch.setattr(pipeline, "STATE_PATH", state)
    monkeypatch.setattr(pipeline, "LOG_DIR", logs)
    monkeypatch.setenv("TEST_PIPELINE_SECRET", "do-not-log-this")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "connected with do-not-log-this", "")

    result = pipeline.run_once(make_args(source), runner=runner)
    assert result == 0
    assert pipeline.load_state(state)["status"] == "SUCCESS"
    log_text = next(logs.glob("*.log")).read_text(encoding="utf-8")
    assert "do-not-log-this" not in log_text
    assert "[REDACTED]" in log_text
