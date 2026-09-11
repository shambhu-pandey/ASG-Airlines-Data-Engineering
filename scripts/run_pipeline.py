"""Run the validated ASG Airlines pipeline in one repeatable command."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "raw" / "UseCase - Airlines.xlsx"
STATE_PATH = ROOT / "data" / "state" / "last_successful_source.json"
LOG_DIR = ROOT / "logs" / "pipeline"


@dataclass(frozen=True)
class Stage:
    name: str
    command: tuple[str, ...]


class PipelineLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("a", encoding="utf-8")

    def write(self, message: str = "") -> None:
        print(message)
        self._stream.write(message + "\n")
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def timestamp() -> str:
    return now_utc().strftime("%Y%m%d_%H%M%S")


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact(text: str) -> str:
    redacted = text
    secret_names = ("PASSWORD", "SECRET", "TOKEN", "KEY", "CONNECTION_STRING")
    for name, value in os.environ.items():
        if value and any(part in name.upper() for part in secret_names):
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def load_state(path: Path = STATE_PATH) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_success_state(
    source: Path,
    file_hash: str,
    path: Path = STATE_PATH,
    azure_status: str = "COMPLETED",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_file": display_path(source),
        "sha256": file_hash,
        "processed_at": now_utc().isoformat(),
        "status": "SUCCESS",
        "azure_migration": azure_status,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def source_is_unchanged(source: Path, file_hash: str, path: Path = STATE_PATH) -> bool:
    state = load_state(path)
    return bool(
        state
        and state.get("status") == "SUCCESS"
        and state.get("source_file") == display_path(source)
        and state.get("sha256") == file_hash
    )


def module_command(module: str, *arguments: str) -> tuple[str, ...]:
    return (sys.executable, "-m", module, *arguments)


def script_command(script: str, *arguments: str) -> tuple[str, ...]:
    return (sys.executable, str(ROOT / "scripts" / script), *arguments)


def build_stages(source: Path, skip_azure: bool, skip_profiling: bool, preflight_azure: bool) -> list[Stage]:
    stages: list[Stage] = [Stage("INGESTION", module_command("src.ingest", str(source)))]
    if not skip_profiling:
        stages.append(Stage("PROFILING", module_command("src.profiling")))
    stages.extend([
        Stage("CLEANING", module_command("src.cleaning")),
        Stage("VALIDATION", module_command("src.validation")),
        Stage("GOLD TRANSFORMATION", module_command("src.transformation")),
    ])
    if not skip_azure:
        preflight = ("--preflight-only",) if preflight_azure else ()
        stages.append(Stage("AZURE CORE SYNC", script_command("sync_local_to_azure.py", "--core-only", *preflight)))
        stages.append(Stage("AZURE GOLD SYNC", script_command("sync_local_to_azure.py", "--gold-only", *preflight)))
        if preflight_azure:
            stages.append(Stage("AZURE CORE SYNC", script_command("sync_local_to_azure.py", "--core-only")))
            stages.append(Stage("AZURE GOLD SYNC", script_command("sync_local_to_azure.py", "--gold-only")))
    return stages


def command_display(command: Sequence[str]) -> str:
    return " ".join(f'"{item}"' if " " in item else item for item in command)


def run_stage(stage: Stage, index: int, total: int, logger: PipelineLogger, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
    started = time.monotonic()
    logger.write(f"\n[{index}/{total}] {stage.name}")
    logger.write(f"Command: {command_display(stage.command)}")
    result = runner(
        list(stage.command),
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    output = redact((result.stdout or "") + (result.stderr or "")).strip()
    if output:
        logger.write(output)
    duration = time.monotonic() - started
    logger.write(f"Stage {stage.name}: exit_code={result.returncode} duration={duration:.2f}s")
    if result.returncode != 0:
        raise RuntimeError(f"Stage {stage.name} failed with exit code {result.returncode}")


def run_once(args: argparse.Namespace, *, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> int:
    source = Path(args.file).expanduser()
    if not source.is_absolute():
        source = ROOT / source
    source = source.resolve()
    if not source.is_file():
        print(f"Source workbook not found: {source}", file=sys.stderr)
        return 2
    if source.name.startswith("~$"):
        print(f"Ignoring temporary Excel lock file: {source}", file=sys.stderr)
        return 0

    file_hash = source_sha256(source)
    log_path = LOG_DIR / f"pipeline_{timestamp()}.log"
    logger = PipelineLogger(log_path)
    started = time.monotonic()
    started_at = now_utc().isoformat()
    stages = build_stages(source, args.skip_azure, args.skip_profiling, args.preflight_azure)
    try:
        logger.write("=" * 60)
        logger.write("ASG AIRLINES DATA PIPELINE")
        logger.write("=" * 60)
        logger.write(f"Source:\nFile: {display_path(source)}\nSHA256: {file_hash}\nStarted: {started_at}")
        logger.write("=" * 60)

        previous_state = load_state(STATE_PATH)
        azure_complete = previous_state and previous_state.get("azure_migration") == "COMPLETED"
        if source_is_unchanged(source, file_hash) and (args.skip_azure or azure_complete) and not args.dry_run:
            logger.write("Source file unchanged. No new pipeline execution required.")
            return 0

        if args.dry_run:
            logger.write("DRY RUN: no pipeline stages will execute and no success state will be written.")
            for index, stage in enumerate(stages, start=1):
                logger.write(f"[{index}/{len(stages)}] {stage.name}: {command_display(stage.command)}")
            return 0

        for index, stage in enumerate(stages, start=1):
            run_stage(stage, index, len(stages), logger, runner)

        azure_status = "SKIPPED" if args.skip_azure else "COMPLETED"
        write_success_state(source, file_hash, STATE_PATH, azure_status)
        logger.write("\n" + "=" * 60)
        logger.write("PIPELINE COMPLETED SUCCESSFULLY")
        logger.write("=" * 60)
        logger.write(f"Source: {display_path(source)}")
        logger.write(f"Run duration: {time.monotonic() - started:.2f}s")
        logger.write(f"Azure migration: {azure_status}")
        logger.write("Final status: SUCCESS")
        logger.write("=" * 60)
        return 0
    except (OSError, RuntimeError) as error:
        logger.write("\n" + "=" * 60)
        logger.write(f"PIPELINE FAILED: {redact(str(error))}")
        logger.write(f"Run duration: {time.monotonic() - started:.2f}s")
        logger.write("Final status: FAILURE")
        logger.write("=" * 60)
        return 1
    finally:
        logger.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ASG Airlines pipeline end to end.")
    parser.add_argument("--file", default=str(DEFAULT_SOURCE), help="Source Excel workbook path.")
    parser.add_argument("--skip-azure", action="store_true", help="Stop after local Gold transformation.")
    parser.add_argument("--skip-profiling", action="store_true", help="Skip the read-only profiling stage.")
    parser.add_argument("--preflight-azure", action="store_true", help="Run read-only Azure checks before migration.")
    parser.add_argument("--dry-run", action="store_true", help="List stages without modifying data or state.")
    parser.add_argument("--watch", action="store_true", help="Poll the configured source for new hashes.")
    parser.add_argument("--poll-interval", type=float, default=30.0, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.watch:
        return run_once(args)
    try:
        while True:
            run_once(args)
            time.sleep(max(args.poll_interval, 1.0))
    except KeyboardInterrupt:
        print("Pipeline watch stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
