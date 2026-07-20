"""Evaluate saved field-verification outcomes without pretending they are unbiased.

Only confirmed_problem and false_alarm rows are usable binary labels. The
result is a pilot estimate; inspections selected from alerts alone are subject
to selection bias, so some randomly chosen non-alert parcels should be checked.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, write_json_atomic  # noqa: E402


def main(client: str, min_labels: int) -> Path:
    directory = CLIENTS_DIR / client
    path = directory / "inspection_feedback.csv"
    feedback = pd.read_csv(path)
    usable = feedback[feedback["outcome"].isin(["confirmed_problem", "false_alarm"])].copy()
    if len(usable) < min_labels:
        raise SystemExit(
            f"Need at least {min_labels} confirmed/false-alarm labels; found {len(usable)}"
        )
    y_true = (usable["outcome"] == "confirmed_problem").astype(int)
    y_pred = usable["system_flagged"].astype(str).str.lower().isin(["true", "1", "yes"]).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    result = {
        "client": client,
        "labels": int(len(usable)),
        "confirmed_problems": int(y_true.sum()),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "warning": (
            "Pilot estimate only. Alert-selected inspections are biased; validate on both "
            "alerted parcels and a random sample of non-alerted parcels."
        ),
    }
    output = directory / "models" / "feedback_evaluation.json"
    write_json_atomic(output, result)
    print(json.dumps(result, indent=2))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--min-labels", type=int, default=20)
    args = parser.parse_args()
    main(args.client, args.min_labels)
