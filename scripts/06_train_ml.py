"""Train the unsupervised canopy anomaly model from a client's history."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, load_json, write_json_atomic  # noqa: E402
from ml_engine import MODEL_FILENAME, save_bundle, train_bundle  # noqa: E402


def main(client: str, contamination: float, seed: int, holdout_days: int) -> Path:
    directory = CLIENTS_DIR / client
    observations = pd.read_csv(directory / "observations.csv")
    fields = load_json(directory / "fields.geojson")
    bundle, training = train_bundle(
        observations, fields, contamination, seed, holdout_days=holdout_days
    )
    model_path = save_bundle(bundle, directory / "models" / MODEL_FILENAME)
    metadata = {
        "model_version": bundle["model_version"],
        "trained_at": bundle["trained_at"],
        "algorithm": "IsolationForest",
        "training_rows": int(len(training)),
        "training_fields": int(training["field_id"].nunique()),
        "date_start": training["date"].min().date().isoformat(),
        "date_end": training["date"].max().date().isoformat(),
        "contamination": contamination,
        "holdout_days": bundle["holdout_days"],
        "training_cutoff": bundle["training_cutoff"],
        "crop_models": sorted(bundle["by_crop"]),
        "features": bundle["features"],
        "limitations": (
            "Unsupervised anomaly ranking, not a crop diagnosis. Validate alerts on the ground."
        ),
    }
    write_json_atomic(directory / "models" / "model_metadata.json", metadata)
    print(json.dumps(metadata, indent=2))
    print(f"\nModel written to {model_path}")
    return model_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--contamination", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--holdout-days", type=int, default=30,
                        help="exclude the most recent days from unsupervised training")
    args = parser.parse_args()
    main(args.client, args.contamination, args.seed, args.holdout_days)
