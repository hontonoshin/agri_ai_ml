"""Detect canopy anomalies against the same-crop cohort and each field's history.

Every rule here produces a statement of the form "this parcel is unusual relative
to X". None produces a diagnosis. See config.SCOPE_STATEMENT.

Two independent reference frames, because they fail in different ways:

  cohort   — the client's other parcels of the same crop, same week. Survives a
             region-wide drought or a late spring, because the reference moves
             with the weather. Needs >= MIN_COHORT parcels to mean anything.
  history  — the parcel's own trailing observations. Works with a single parcel,
             but crop rotation and normal senescence look like collapse, so the
             crop calendar gates it.

Outputs (under clients/<client>/):
    field_state.csv   latest state per field: indices, cohort rank, trend
    anomalies.csv     one row per (field, rule) currently firing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import CLIENTS_DIR, load_json, write_json_atomic  # noqa: E402
from config import (  # noqa: E402
    CROP_CALENDARS,
    INDICES,
    MIN_COHORT,
    MIN_COHORT_SCALE,
    RULES,
    SEVERITY_ORDER,
)
from ml_engine import MODEL_FILENAME, score_latest  # noqa: E402


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def field_crops(features: list[dict]) -> pd.DataFrame:
    rows = []
    for feature in features:
        properties = feature["properties"]
        field_id = int(properties["field_id"])
        for year, crop in (properties.get("seasons") or {}).items():
            rows.append({
                "field_id": field_id,
                "year": int(year),
                "crop": crop if crop in CROP_CALENDARS else "other",
                "name": properties.get("name", str(field_id)),
                "crop_source": properties.get("crop_source", "declared"),
            })
    if not rows:
        raise ValueError("No seasons declared on any field — see 01_setup_check.py")
    return pd.DataFrame(rows)


def prepare(observations: pd.DataFrame, crops: pd.DataFrame) -> pd.DataFrame:
    frame = observations.copy()
    # Legacy pipelines may contain NDVI only. Missing optional indices remain
    # genuinely missing; they are never replaced with invented measurements.
    for index_name in INDICES:
        if index_name not in frame.columns:
            frame[index_name] = float("nan")
    if "valid_frac" not in frame.columns:
        frame["valid_frac"] = float("nan")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["year"] = frame["date"].dt.year
    frame["doy"] = frame["date"].dt.dayofyear
    frame = frame.merge(crops, on=["field_id", "year"], how="inner")
    if frame.empty:
        raise ValueError("No observations overlap a declared season")
    return frame.sort_values(["field_id", "date"])


def composite_week(frame: pd.DataFrame, as_of: pd.Timestamp, window_days: int) -> pd.DataFrame:
    """Latest observation per field within the reporting window.

    Sentinel-2 revisit is ~5 days and clouds remove some of those, so fields are
    not observed on the same day. A window, not a date, is the honest unit.
    """
    window = frame[(frame["date"] <= as_of) & (frame["date"] > as_of - pd.Timedelta(days=window_days))]
    if window.empty:
        return window
    return window.sort_values("date").groupby("field_id", as_index=False).last()


# --------------------------------------------------------------------------- #
# cohort statistics
# --------------------------------------------------------------------------- #
def add_cohort(current: pd.DataFrame) -> pd.DataFrame:
    """Rank each field within its same-crop cohort for this window."""
    current = current.copy()
    for index_name in INDICES:
        current[f"{index_name}_pct"] = pd.NA
        current[f"{index_name}_cohort_median"] = pd.NA
        current[f"{index_name}_relative"] = pd.NA
        current[f"{index_name}_z"] = pd.NA
    current["cohort_size"] = 0

    for crop, block in current.groupby("crop"):
        current.loc[block.index, "cohort_size"] = len(block)
        if len(block) < MIN_COHORT:
            continue
        for index_name in INDICES:
            values = block[index_name].dropna()
            if len(values) < MIN_COHORT:
                continue
            median = float(values.median())
            # MAD -> normal-consistent scale. Robust to the very outliers we are
            # hunting, unlike a standard deviation, which they inflate.
            mad = float((values - median).abs().median())
            scale = max(1.4826 * mad, MIN_COHORT_SCALE)
            current.loc[values.index, f"{index_name}_pct"] = values.rank(pct=True, method="average")
            current.loc[block.index, f"{index_name}_cohort_median"] = median
            current.loc[block.index, f"{index_name}_z"] = (block[index_name] - median) / scale
            if median > 0:
                current.loc[block.index, f"{index_name}_relative"] = block[index_name] / median
    return current


def add_history(current: pd.DataFrame, frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Trailing mean and season peak from each field's own record."""
    window_obs = RULES["vigour_drop_vs_self"]["window_obs"]
    current = current.copy()
    trailing: list[float] = []
    peaks: list[float] = []
    peak_doys: list[float] = []

    for row in current.itertuples():
        history = frame[
            (frame["field_id"] == row.field_id)
            & (frame["date"] < row.date)
            & (frame["year"] == row.year)
        ].sort_values("date")
        previous = history["ndvi"].dropna()
        trailing.append(float(previous.tail(window_obs).mean()) if len(previous) else float("nan"))

        season = frame[(frame["field_id"] == row.field_id) & (frame["year"] == row.year)]
        season_ndvi = season["ndvi"].dropna()
        if len(season_ndvi):
            peaks.append(float(season_ndvi.max()))
            peak_doys.append(float(season.loc[season_ndvi.idxmax(), "doy"]))
        else:
            peaks.append(float("nan"))
            peak_doys.append(float("nan"))

    current["ndvi_trailing_mean"] = trailing
    current["season_peak_ndvi"] = peaks
    current["season_peak_doy"] = peak_doys
    current["ndvi_change"] = current["ndvi"] - current["ndvi_trailing_mean"]
    return current


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #
def in_window(doy: float, window: tuple[int, int]) -> bool:
    return window[0] <= doy <= window[1]


def evaluate(row) -> tuple[list[dict], str]:
    """Apply every rule to one field-window.

    Returns (fired_rules, coverage). Coverage records which rule families could
    actually run — an empty fired list means "nothing wrong" only when coverage
    says the rules ran. Silence and an all-clear are different facts and must
    never be collapsed into the same zero.
    """
    calendar = CROP_CALENDARS.get(row["crop"], CROP_CALENDARS["other"])
    doy = float(row["doy"])
    fired: list[dict] = []
    # Never splice the raw crop key into prose: "cotton dalalaringiz" reads as a
    # broken translation to the client, which is what the key looked like before.
    crop_en = calendar["label_en"].lower()
    crop_uz = calendar["label_uz"].lower()

    def add(rule: str, detail_en: str, detail_uz: str, severity: str) -> None:
        fired.append({
            "field_id": int(row["field_id"]),
            "name": row["name"],
            "crop": row["crop"],
            "date": row["date"].date().isoformat(),
            "rule": rule,
            "severity": severity,
            "detail_en": detail_en,
            "detail_uz": detail_uz,
        })

    # A mixed set of fields with no crop labels has no defensible common crop
    # calendar or same-crop cohort. The learned seasonal model may still rank
    # unusual trajectories, but crop-specific expert rules must stay silent.
    if row.get("crop") == "other" or row.get("crop_source") == "unspecified_default":
        return fired, "crop_unspecified"

    senescing = in_window(doy, calendar["senescence"])
    # Outside the crop's growing window there is nothing to say: before emergence
    # the field is legitimately bare, and after senescence it is harvested. Both
    # look like collapse to every rule below, so no rule may run here.
    if doy < calendar["emergence"][0] or doy > calendar["senescence"][1]:
        return fired, "outside_growing_window"

    # --- cohort: low vigour -------------------------------------------------
    rule = RULES["low_vigour_vs_cohort"]
    percentile = row.get("ndvi_pct")
    relative = row.get("ndvi_relative")
    z_score = row.get("ndvi_z")
    if (
        pd.notna(z_score)
        and pd.notna(relative)
        and float(z_score) <= rule["max_z"]
        and float(relative) <= rule["max_relative"]
        and not senescing
    ):
        add(
            "low_vigour_vs_cohort",
            f"NDVI {row['ndvi']:.2f} is {(1 - float(relative)) * 100:.0f}% below the median of "
            f"your {crop_en} parcels this week ({float(row['ndvi_cohort_median']):.2f}) — a "
            f"clear outlier, not ordinary spread.",
            f"NDVI {row['ndvi']:.2f} — shu haftadagi {crop_uz} dalalaringiz medianasidan "
            f"({float(row['ndvi_cohort_median']):.2f}) {(1 - float(relative)) * 100:.0f}% past; "
            "bu oddiy tarqalish emas, aniq chetlanish.",
            rule["severity"],
        )

    # --- history: drop vs self ---------------------------------------------
    rule = RULES["vigour_drop_vs_self"]
    change = row.get("ndvi_change")
    if pd.notna(change) and float(change) <= -rule["min_drop"] and not senescing:
        add(
            "vigour_drop_vs_self",
            f"NDVI fell {abs(float(change)):.2f} against this parcel's own recent mean "
            f"({float(row['ndvi_trailing_mean']):.2f} -> {row['ndvi']:.2f}), outside the "
            f"expected senescence window for {crop_en}.",
            f"NDVI shu dalaning o'z o'rtachasiga nisbatan {abs(float(change)):.2f} ga tushdi "
            f"({float(row['ndvi_trailing_mean']):.2f} -> {row['ndvi']:.2f}); bu {crop_uz} "
            "uchun kutilgan qurish davridan tashqarida.",
            rule["severity"],
        )

    # --- cohort: canopy moisture -------------------------------------------
    rule = RULES["low_canopy_moisture"]
    moisture_z = row.get("ndmi_z")
    moisture_relative = row.get("ndmi_relative")
    if (
        pd.notna(moisture_z)
        and pd.notna(moisture_relative)
        and float(moisture_z) <= rule["max_z"]
        and float(moisture_relative) <= rule["max_relative"]
        and pd.notna(row.get("ndvi"))
        and float(row["ndvi"]) >= rule["min_ndvi"]
        and not senescing
    ):
        add(
            "low_canopy_moisture",
            f"Canopy moisture (NDMI {row['ndmi']:.2f}) is {(1 - float(moisture_relative)) * 100:.0f}% "
            f"below the median of your {crop_en} parcels this week, while canopy density is "
            f"normal. This describes the canopy, not the soil, and is not an irrigation instruction.",
            f"O'simlik namligi (NDMI {row['ndmi']:.2f}) shu haftadagi {crop_uz} dalalaringiz "
            f"medianasidan {(1 - float(moisture_relative)) * 100:.0f}% past, o'simlik zichligi esa "
            "normal. Bu tuproqni emas, o'simlik qoplamini tavsiflaydi va sug'orish ko'rsatmasi emas.",
            rule["severity"],
        )

    # --- calendar: late emergence ------------------------------------------
    rule = RULES["late_emergence"]
    if (
        doy > calendar["emergence"][1] + rule["days_after_window"]
        and doy < calendar["peak"][1]
        and pd.notna(row.get("ndvi"))
        and float(row["ndvi"]) < rule["ndvi_below"]
        # Must never have had a canopy this season. Without this, a parcel that
        # emerged normally and then collapsed is mislabelled as never planted.
        and pd.notna(row.get("season_peak_ndvi"))
        and float(row["season_peak_ndvi"]) < rule["peak_below"]
    ):
        add(
            "late_emergence",
            f"NDVI {row['ndvi']:.2f} on day {int(doy)} — still bare "
            f"{int(doy - calendar['emergence'][1])} days after the expected emergence "
            f"window for {crop_en} closed.",
            f"{int(doy)}-kunda NDVI {row['ndvi']:.2f} — {crop_uz} uchun kutilgan unib "
            f"chiqish davri yopilganidan {int(doy - calendar['emergence'][1])} kun o'tib ham "
            "dala bo'sh ko'rinmoqda.",
            rule["severity"],
        )

    # --- calendar: early senescence ----------------------------------------
    rule = RULES["early_senescence"]
    peak = row.get("season_peak_ndvi")
    if (
        pd.notna(peak)
        and float(peak) >= 0.40
        and doy < calendar["senescence"][0] - rule["days_before_window"]
        and doy > float(row.get("season_peak_doy") or 0)
        and float(peak) - float(row["ndvi"]) >= rule["min_drop_from_peak"]
    ):
        add(
            "early_senescence",
            f"NDVI is {float(peak) - float(row['ndvi']):.2f} below this season's peak "
            f"({float(peak):.2f}) on day {int(doy)}, "
            f"{int(calendar['senescence'][0] - doy)} days before {crop_en} senescence "
            "is expected to begin.",
            f"{int(doy)}-kunda NDVI shu mavsum eng yuqori qiymatidan ({float(peak):.2f}) "
            f"{float(peak) - float(row['ndvi']):.2f} past; {crop_uz} qurishi kutilgan "
            f"davrdan {int(calendar['senescence'][0] - doy)} kun oldin.",
            rule["severity"],
        )

    # Which rule families could actually run on this parcel. An empty fired list
    # means "nothing wrong" only when this says the rules ran at all.
    has_cohort = pd.notna(row.get("ndvi_z")) and pd.notna(row.get("ndvi_relative"))
    has_history = pd.notna(row.get("ndvi_trailing_mean"))
    if has_cohort and has_history:
        coverage = "full"
    elif has_cohort:
        coverage = "cohort_only"
    elif has_history:
        coverage = "history_only"
    else:
        coverage = "not_evaluated"
    return fired, coverage


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(client: str, as_of: str | None, window_days: int,
         ml_percentile: float = 0.97) -> Path:
    directory = CLIENTS_DIR / client
    observations = pd.read_csv(directory / "observations.csv")
    if observations.empty:
        raise SystemExit("observations.csv is empty — run 02_fetch_indices.py first")
    fields = load_json(directory / "fields.geojson")

    crops = field_crops(fields.get("features", []))
    frame = prepare(observations, crops)
    as_of_ts = pd.Timestamp(as_of) if as_of else frame["date"].max()

    current = composite_week(frame, as_of_ts, window_days)
    if current.empty:
        raise SystemExit(
            f"No clear observations in the {window_days} days before {as_of_ts.date()}. "
            "This is normal under persistent cloud; widen --window-days or wait."
        )
    current = add_cohort(current)
    current = add_history(current, frame, as_of_ts)

    # Learned multivariate reference. The pipeline remains usable before a
    # model is trained, but the summary says so explicitly—rules are not passed
    # off as AI. Train once with 06_train_ml.py, then retrain periodically.
    model_path = directory / "models" / MODEL_FILENAME
    ml_status = "not_trained"
    if model_path.exists():
        try:
            ml_scores = score_latest(
                observations, fields, model_path, as_of_ts, window_days,
                alert_percentile=ml_percentile,
            )
            current = current.merge(ml_scores, on=["field_id", "date"], how="left")
            ml_status = "active"
        except Exception as exc:
            ml_status = f"error:{type(exc).__name__}"
            print(f"  WARNING: ML scoring disabled: {type(exc).__name__}: {exc}")
    for column, default in {
        "ml_risk": float("nan"),
        "ml_raw_score": float("nan"),
        "ml_flag": False,
        "ml_scope": "none",
        "ml_explanation": "",
        "ml_model_version": "",
    }.items():
        if column not in current:
            current[column] = default

    fired: list[dict] = []
    coverage: list[str] = []
    for _, row in current.iterrows():
        rules, status = evaluate(row)
        fired.extend(rules)
        coverage.append(status)
        if pd.notna(row.get("ml_flag")) and bool(row.get("ml_flag", False)):
            risk = 100.0 * float(row["ml_risk"])
            explanation = str(row.get("ml_explanation") or "multivariate temporal pattern")
            fired.append({
                "field_id": int(row["field_id"]),
                "name": row["name"],
                "crop": row["crop"],
                "date": row["date"].date().isoformat(),
                "rule": "ml_multivariate_anomaly",
                "severity": "high" if risk >= 99.0 else "medium",
                "detail_en": (
                    f"The learned multivariate model ranks this parcel in the most unusual "
                    f"{100.0 - risk:.1f}% of historical patterns. Main contributors: "
                    f"{explanation}. This is an inspection priority, not a diagnosis."
                ),
                "detail_uz": (
                    f"O'rgatilgan ko'p-omilli model bu dalani tarixiy holatlarning eng noodatiy "
                    f"{100.0 - risk:.1f}% qatoriga kiritdi. Asosiy omillar: {explanation}. "
                    "Bu tekshirish ustuvorligi, tashxis emas."
                ),
            })
    current["coverage"] = coverage
    if ml_status == "active":
        ml_only = (current["coverage"] == "crop_unspecified") & current["ml_risk"].notna()
        current.loc[ml_only, "coverage"] = "ml_only"
    anomalies = pd.DataFrame(fired)
    if len(anomalies):
        anomalies["_rank"] = anomalies["severity"].map(SEVERITY_ORDER).fillna(3)
        anomalies = anomalies.sort_values(["_rank", "field_id"]).drop(columns="_rank")

    state = current.copy()
    state["date"] = state["date"].dt.date.astype(str)
    state.to_csv(directory / "field_state.csv", index=False)
    anomalies.to_csv(directory / "anomalies.csv", index=False)

    thin = sorted(set(current.loc[current["cohort_size"] < MIN_COHORT, "crop"]))
    coverage_counts = current["coverage"].value_counts().to_dict()
    evaluated_states = ["full", "cohort_only", "history_only", "ml_only"]
    evaluated = int(current["coverage"].isin(evaluated_states).sum())
    unevaluated = current[~current["coverage"].isin(evaluated_states)]
    # Why each unchecked parcel was skipped, in the client's terms.
    reasons: dict[str, list[str]] = {}
    for row in unevaluated.itertuples():
        reasons.setdefault(row.coverage, []).append(str(row.name))
    write_json_atomic(directory / "anomaly_summary.json", {
        "as_of": as_of_ts.date().isoformat(),
        "window_days": window_days,
        "fields_observed": int(len(current)),
        "fields_total": int(crops["field_id"].nunique()),
        "fields_evaluated": evaluated,
        "coverage_counts": coverage_counts,
        "unevaluated_reasons": reasons,
        "anomalies": int(len(anomalies)),
        "by_rule": anomalies["rule"].value_counts().to_dict() if len(anomalies) else {},
        "by_severity": anomalies["severity"].value_counts().to_dict() if len(anomalies) else {},
        "cohort_sizes": current.groupby("crop")["cohort_size"].max().to_dict(),
        "crops_without_cohort": thin,
        "min_cohort": MIN_COHORT,
        "ml_status": ml_status,
        "ml_model": MODEL_FILENAME if ml_status == "active" else None,
        "ml_alert_percentile": ml_percentile,
        "ml_fields_scored": int(current["ml_risk"].notna().sum()),
        "ml_fields_flagged": int(current["ml_flag"].fillna(False).astype(bool).sum()),
    })

    print(f"As of {as_of_ts.date()} ({window_days}-day window)")
    print(f"  observed:  {len(current)}/{crops['field_id'].nunique()} fields")
    print(f"  evaluated: {evaluated}/{len(current)} observed fields")
    if evaluated < len(current):
        for reason, names in reasons.items():
            print(f"    NOT EVALUATED ({reason}): {len(names)} — {', '.join(names[:6])}")
    print(f"  anomalies: {len(anomalies)}" + ("" if evaluated else "  <- zero because nothing was checked"))
    if len(anomalies):
        print(f"  by rule: {anomalies['rule'].value_counts().to_dict()}")
    if thin:
        print(f"  NOTE: cohort rules suppressed (<{MIN_COHORT} parcels): {', '.join(thin)}")
    print(f"  ML status: {ml_status}; scored {current['ml_risk'].notna().sum()}/{len(current)}")
    return directory / "anomalies.csv"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    parser.add_argument("--as-of", default=None, help="default: latest observation")
    parser.add_argument("--window-days", type=int, default=10)
    parser.add_argument("--ml-percentile", type=float, default=0.97,
                        help="alert above this historical anomaly percentile")
    args = parser.parse_args()
    main(args.client, args.as_of, args.window_days, args.ml_percentile)
