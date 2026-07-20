"""Machine-learning layer for multivariate Sentinel-2 parcel time series.

The existing expert rules remain the safety/explanation layer.  This module
adds a genuinely learned reference distribution: an Isolation Forest learns
normal combinations of vegetation level, change, season and cohort position
from each client's historical observations.  It does not diagnose causes.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


MODEL_FILENAME = "canopy_isolation_forest.joblib"
MODEL_VERSION = "2.0"
MIN_TRAIN_ROWS = 100
MIN_TRAIN_FIELDS = 8
INDEX_NAMES = ("ndvi", "ndre", "ndmi", "evi", "savi")

FEATURES = [
    *INDEX_NAMES,
    "doy_sin", "doy_cos",
    *(f"{name}_{suffix}" for name in INDEX_NAMES
      for suffix in ("delta_1", "delta_2", "roll_mean_3")),
    "ndvi_from_previous_peak",
    *(f"{name}_cohort_z" for name in INDEX_NAMES),
]

FEATURE_LABELS = {
    "ndvi": "current canopy vigour",
    "ndre": "current red-edge vigour",
    "ndmi": "current canopy moisture",
    "evi": "current enhanced vegetation",
    "savi": "current soil-background-adjusted vegetation",
    "doy_sin": "seasonal timing",
    "doy_cos": "seasonal timing",
    "ndvi_delta_1": "recent NDVI change",
    "ndvi_delta_2": "two-observation NDVI change",
    "ndvi_roll_mean_3": "recent NDVI baseline",
    "ndre_delta_1": "recent NDRE change",
    "ndre_delta_2": "two-observation NDRE change",
    "ndre_roll_mean_3": "recent NDRE baseline",
    "ndmi_delta_1": "recent NDMI change",
    "ndmi_delta_2": "two-observation NDMI change",
    "ndmi_roll_mean_3": "recent NDMI baseline",
    "evi_delta_1": "recent EVI change",
    "evi_delta_2": "two-observation EVI change",
    "evi_roll_mean_3": "recent EVI baseline",
    "savi_delta_1": "recent SAVI change",
    "savi_delta_2": "two-observation SAVI change",
    "savi_roll_mean_3": "recent SAVI baseline",
    "ndvi_from_previous_peak": "change from the earlier seasonal peak",
    "ndvi_cohort_z": "NDVI position within the crop cohort",
    "ndmi_cohort_z": "NDMI position within the crop cohort",
    "ndre_cohort_z": "NDRE position within the crop cohort",
    "evi_cohort_z": "EVI position within the crop cohort",
    "savi_cohort_z": "SAVI position within the crop cohort",
}


def crop_table(fields: dict) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in fields.get("features", []):
        properties = feature.get("properties", {})
        field_id = int(properties["field_id"])
        for year, crop in (properties.get("seasons") or {}).items():
            rows.append({
                "field_id": field_id,
                "year": int(year),
                "crop": str(crop),
                "name": properties.get("name", str(field_id)),
            })
    if not rows:
        raise ValueError("No crop seasons declared in fields.geojson")
    return pd.DataFrame(rows)


def _robust_z(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() < 4:
        return pd.Series(np.nan, index=values.index)
    median = float(numeric.median())
    mad = float((numeric - median).abs().median())
    scale = max(1.4826 * mad, 0.03)
    return (numeric - median) / scale


def build_feature_table(observations: pd.DataFrame, fields: dict) -> pd.DataFrame:
    """Create leakage-aware temporal features for every field observation."""
    frame = observations.copy()
    for index_name in INDEX_NAMES:
        if index_name not in frame.columns:
            frame[index_name] = float("nan")
    if "valid_frac" not in frame.columns:
        frame["valid_frac"] = float("nan")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["field_id", "date"]).copy()
    frame["field_id"] = frame["field_id"].astype(int)
    frame["year"] = frame["date"].dt.year
    frame["doy"] = frame["date"].dt.dayofyear.astype(float)
    frame = frame.merge(crop_table(fields), on=["field_id", "year"], how="inner")
    frame = frame.sort_values(["field_id", "year", "date"]).reset_index(drop=True)
    if frame.empty:
        raise ValueError("No observations overlap declared field seasons")

    frame["doy_sin"] = np.sin(2.0 * np.pi * frame["doy"] / 365.25)
    frame["doy_cos"] = np.cos(2.0 * np.pi * frame["doy"] / 365.25)
    grouped = frame.groupby(["field_id", "year"], sort=False, group_keys=False)
    frame["days_since_previous"] = grouped["date"].diff().dt.days

    for index_name in INDEX_NAMES:
        frame[index_name] = pd.to_numeric(frame[index_name], errors="coerce")
        grouped = frame.groupby(["field_id", "year"], sort=False, group_keys=False)
        frame[f"{index_name}_delta_1"] = grouped[index_name].diff(1)
        frame[f"{index_name}_delta_2"] = grouped[index_name].diff(2)
        frame[f"{index_name}_roll_mean_3"] = grouped[index_name].transform(
            lambda series: series.shift(1).rolling(3, min_periods=2).mean()
        )

    grouped = frame.groupby(["field_id", "year"], sort=False, group_keys=False)
    previous_peak = grouped["ndvi"].transform(lambda series: series.shift(1).cummax())
    frame["ndvi_from_previous_peak"] = frame["ndvi"] - previous_peak

    # A reporting week is the spatial reference because clouds often shift one
    # parcel's usable acquisition by several days. Exact-date grouping would
    # leave many parcels without a cohort.
    frame["week"] = frame["date"].dt.to_period("W").astype(str)
    for index_name in INDEX_NAMES:
        frame[f"{index_name}_cohort_z"] = frame.groupby(["crop", "week"])[index_name].transform(_robust_z)
    for name in FEATURES:
        frame[name] = pd.to_numeric(frame.get(name), errors="coerce")
    return frame


def _fit_scope(table: pd.DataFrame, contamination: float, seed: int) -> dict[str, Any]:
    matrix = table[FEATURES]
    pipeline = Pipeline([
        # keep_empty_features preserves the schema for NDVI-only legacy data;
        # completely absent NDRE/NDMI features become neutral zeros after
        # scaling and therefore cannot create false evidence.
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
        ("model", IsolationForest(
            n_estimators=400,
            contamination=contamination,
            random_state=seed,
            n_jobs=-1,
        )),
    ])
    pipeline.fit(matrix)
    transformed = pipeline[:-1].transform(matrix)
    raw_anomaly = -pipeline.named_steps["model"].score_samples(transformed)
    median = matrix.median(numeric_only=True).reindex(FEATURES).fillna(0.0)
    mad = matrix.sub(median).abs().median(numeric_only=True).reindex(FEATURES)
    scale = (1.4826 * mad).fillna(0.0).clip(lower=0.03)
    return {
        "pipeline": pipeline,
        "reference_scores": np.sort(raw_anomaly),
        "feature_median": median.to_dict(),
        "feature_scale": scale.to_dict(),
        "rows": int(len(table)),
        "fields": int(table["field_id"].nunique()),
    }


def train_bundle(
    observations: pd.DataFrame,
    fields: dict,
    contamination: float = 0.03,
    seed: int = 42,
    holdout_days: int = 30,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if not 0.001 <= contamination <= 0.20:
        raise ValueError("contamination must be between 0.001 and 0.20")
    table = build_feature_table(observations, fields)
    # Two earlier observations are needed for meaningful temporal behaviour.
    cutoff = table["date"].max() - pd.Timedelta(days=max(holdout_days, 0))
    training = table[
        table["ndvi_delta_2"].notna()
        & (table["date"] <= cutoff)
    ].copy()
    if len(training) < MIN_TRAIN_ROWS or training["field_id"].nunique() < MIN_TRAIN_FIELDS:
        raise ValueError(
            f"Need at least {MIN_TRAIN_ROWS} usable rows and {MIN_TRAIN_FIELDS} fields; "
            f"found {len(training)} rows and {training['field_id'].nunique()} fields"
        )

    bundle: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "features": FEATURES,
        "contamination": contamination,
        "training_cutoff": cutoff.date().isoformat(),
        "holdout_days": int(max(holdout_days, 0)),
        "global": _fit_scope(training, contamination, seed),
        "by_crop": {},
    }
    for crop, block in training.groupby("crop"):
        if len(block) >= MIN_TRAIN_ROWS and block["field_id"].nunique() >= MIN_TRAIN_FIELDS:
            bundle["by_crop"][str(crop)] = _fit_scope(block, contamination, seed)
    return bundle, training


def save_bundle(bundle: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    joblib.dump(bundle, temporary)
    temporary.replace(path)
    return path


def load_bundle(path: str | Path) -> dict[str, Any]:
    bundle = joblib.load(path)
    if bundle.get("features") != FEATURES:
        raise ValueError("Model feature schema differs from this code; retrain the model")
    return bundle


def _explanation(row: pd.Series, scope: dict[str, Any]) -> str:
    median = pd.Series(scope["feature_median"])
    scale = pd.Series(scope["feature_scale"]).replace(0, 0.03)
    values = pd.to_numeric(row[FEATURES], errors="coerce")
    deviation = ((values - median) / scale).abs().dropna().sort_values(ascending=False)
    labels: list[str] = []
    for feature in deviation.index:
        label = FEATURE_LABELS.get(feature, feature)
        if label not in labels:
            labels.append(label)
        if len(labels) == 3:
            break
    return ", ".join(labels) if labels else "multivariate temporal pattern"


def score_latest(
    observations: pd.DataFrame,
    fields: dict,
    model_path: str | Path,
    as_of: pd.Timestamp,
    window_days: int,
    alert_percentile: float = 0.97,
) -> pd.DataFrame:
    """Score the latest clear observation for every parcel."""
    bundle = load_bundle(model_path)
    table = build_feature_table(observations, fields)
    window = table[
        (table["date"] <= as_of)
        & (table["date"] > as_of - pd.Timedelta(days=window_days))
    ]
    latest = window.sort_values("date").groupby("field_id", as_index=False).last()
    output: list[dict[str, Any]] = []
    for crop, block in latest.groupby("crop"):
        model_scope = bundle["by_crop"].get(str(crop), bundle["global"])
        scope_name = str(crop) if str(crop) in bundle["by_crop"] else "global"
        transformed = model_scope["pipeline"][:-1].transform(block[FEATURES])
        raw = -model_scope["pipeline"].named_steps["model"].score_samples(transformed)
        reference = np.asarray(model_scope["reference_scores"])
        risks = np.searchsorted(reference, raw, side="right") / max(len(reference), 1)
        for (_, row), raw_score, risk in zip(block.iterrows(), raw, risks):
            reason = _explanation(row, model_scope)
            output.append({
                "field_id": int(row["field_id"]),
                "date": row["date"],
                "ml_risk": round(float(risk), 4),
                "ml_raw_score": round(float(raw_score), 6),
                "ml_flag": bool(risk >= alert_percentile),
                "ml_scope": scope_name,
                "ml_explanation": reason,
                "ml_model_version": bundle["model_version"],
            })
    return pd.DataFrame(output)
