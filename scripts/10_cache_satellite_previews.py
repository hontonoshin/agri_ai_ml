"""Resume-safe pre-caching of true-colour previews for a configured region."""
from __future__ import annotations

import argparse
import concurrent.futures
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from scripts.common import representative_point  # noqa: E402
from telegram_bot.regions import RegionRegistry  # noqa: E402
from telegram_bot.satellite import satellite_preview  # noqa: E402
from telegram_bot.settings import Settings  # noqa: E402


def main(region_id: str, workers: int, limit: int | None) -> None:
    settings = Settings.load(require_token=False)
    registry = RegionRegistry(settings)
    selected = [(entry, index) for entry, index in registry.regions if str(entry["id"]) == region_id]
    if not selected:
        raise SystemExit(f"Unknown region {region_id!r}")
    entry, index = selected[0]
    region_settings = settings.for_client(str(entry["client"]))
    fields = index.fields[:limit] if limit else index.fields
    print(f"Caching up to {len(fields):,} satellite previews with {workers} workers")

    def cache(field):
        existing = region_settings.bot_data / "satellite_cache" / region_settings.client / f"field_{field.field_id}.png"
        if existing.exists():
            return "cached", field.field_id
        lat, lon = representative_point(field.geometry)
        output = Path(tempfile.gettempdir()) / f"agri_preview_{region_id}_{field.field_id}.png"
        try:
            satellite_preview(region_settings, field, lat, lon, output)
            return "downloaded", field.field_id
        finally:
            output.unlink(missing_ok=True)

    counts = {"cached": 0, "downloaded": 0, "failed": 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(cache, field): field.field_id for field in fields}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                status, field_id = future.result()
                counts[status] += 1
            except Exception as exc:
                counts["failed"] += 1
                print(f"field {futures[future]}: {type(exc).__name__}: {exc}")
            if number % 100 == 0 or number == len(fields):
                print(f"{number:,}/{len(fields):,}: {counts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="test with a small number first")
    args = parser.parse_args()
    main(args.region, args.workers, args.limit)
