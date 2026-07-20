"""Run the weekly cycle for one or all clients: fetch -> detect -> report.

Designed for cron. Exit code is non-zero if any client fails, so the scheduler
notices. Each client is independent: one failure does not stop the others.

    0 4 * * 1  cd /srv/agri && .venv/bin/python scripts/05_run_weekly.py --all
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, ensure_dir, latest_available_date, load_json, write_json_atomic  # noqa: E402


def discover_clients() -> list[str]:
    if not CLIENTS_DIR.exists():
        return []
    return sorted(
        directory.name
        for directory in CLIENTS_DIR.iterdir()
        if directory.is_dir() and (directory / "client.json").exists()
    )


def run_step(command: list[str], label: str, log_path: Path) -> None:
    print(f"\n=== {label} ===")
    started = time.time()
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n=== {label} ===\n$ {' '.join(command)}\n")
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        code = process.wait()
        log.write(f"exit={code}; elapsed={time.time() - started:.1f}s\n")
    if code != 0:
        raise RuntimeError(f"Step '{label}' failed with exit code {code}")


def needs_run(client: str, force: bool, latency_days: int) -> bool:
    """Skip when no new imagery can exist since the last successful run.

    openEO quota is the binding constraint, so a no-op run is not free.
    """
    if force:
        return True
    manifest_path = CLIENTS_DIR / client / "last_run.json"
    if not manifest_path.exists():
        return True
    manifest = load_json(manifest_path, default={})
    if manifest.get("status") != "complete":
        return True
    last_end = manifest.get("data_through")
    if not last_end:
        return True
    return dt.date.fromisoformat(last_end) < latest_available_date(latency_days)


def process_client(client: str, window_days: int, language: str | None,
                   headless: bool, latency_days: int, chunk_size: int) -> dict:
    directory = CLIENTS_DIR / client
    log_dir = ensure_dir(directory / "logs")
    started = dt.datetime.now(dt.timezone.utc)
    log_path = log_dir / f"{started:%Y%m%dT%H%M%S}.log"
    python = sys.executable

    manifest = {"client": client, "status": "running", "started_at": started.isoformat()}
    write_json_atomic(directory / "last_run.json", manifest)

    try:
        fetch = [python, str(HERE / "02_fetch_indices.py"), "--client", client,
                 "--chunk-size", str(chunk_size), "--latency-days", str(latency_days)]
        if headless:
            fetch.append("--headless")
        run_step(fetch, f"{client}: fetch indices", log_path)

        run_step([python, str(HERE / "03_anomalies.py"), "--client", client,
                  "--window-days", str(window_days)],
                 f"{client}: detect anomalies", log_path)

        report = [python, str(HERE / "04_report.py"), "--client", client]
        if language:
            report += ["--language", language]
        run_step(report, f"{client}: build report", log_path)

        summary = load_json(directory / "anomaly_summary.json")
        completed = dt.datetime.now(dt.timezone.utc)
        manifest.update({
            "status": "complete",
            "completed_at": completed.isoformat(),
            "duration_seconds": round((completed - started).total_seconds(), 1),
            "data_through": summary["as_of"],
            "fields_observed": summary["fields_observed"],
            "fields_total": summary["fields_total"],
            "anomalies": summary["anomalies"],
            "report": str(directory / "reports" / "latest" / "report.md"),
            "log": str(log_path),
        })
        write_json_atomic(directory / "last_run.json", manifest)
        return manifest
    except Exception as exc:
        manifest.update({
            "status": "failed",
            "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
            "log": str(log_path),
        })
        write_json_atomic(directory / "last_run.json", manifest)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("\n" + traceback.format_exc())
        raise


def main(args: argparse.Namespace) -> int:
    clients = discover_clients() if args.all else [args.client]
    if not clients or clients == [None]:
        print("No clients found. Create clients/<name>/client.json and fields.geojson.")
        return 1

    results: list[tuple[str, str]] = []
    for index, client in enumerate(clients, 1):
        print(f"\n{'=' * 68}\n[{index}/{len(clients)}] CLIENT: {client}\n{'=' * 68}")
        if not needs_run(client, args.force, args.latency_days):
            print("  no new imagery since the last successful run; skipping")
            results.append((client, "skipped"))
            continue
        try:
            manifest = process_client(client, args.window_days, args.language,
                                      args.headless, args.latency_days, args.chunk_size)
            results.append((client, f"ok ({manifest['anomalies']} anomalies)"))
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            results.append((client, "FAILED"))

    print(f"\n{'=' * 68}")
    for client, outcome in results:
        print(f"  {client:24s} {outcome}")
    failures = sum(1 for _, outcome in results if outcome == "FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--window-days", type=int, default=10)
    parser.add_argument("--language", default=None, choices=["uz", "ru", "en"])
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--latency-days", type=int, default=5)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--force", action="store_true", help="run even without new imagery")
    args = parser.parse_args()
    if not args.all and not args.client:
        parser.error("pass --client <name> or --all")
    raise SystemExit(main(args))
