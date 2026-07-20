"""Analyze one selected field against the prepared regional ML baseline."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import pandas as pd

from .air_quality import AirQualityProvider, AirQualityResult
from .copernicus import ObservationProvider
from .field_lookup import FieldMatch
from .settings import Settings


@dataclass
class AnalysisResult:
    field: FieldMatch
    crop: str
    observations: pd.DataFrame
    source: str
    latest_date: str
    latest_ndvi: float
    previous_ndvi: float | None
    peak_ndvi: float
    change: float | None
    anomaly_percentile: float
    alert: bool
    explanation: str
    confidence: str
    indices: dict[str, float | None]
    index_changes: dict[str, float | None]
    air_quality: AirQualityResult | None
    air_quality_error: str | None


class AnalysisService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = ObservationProvider(settings)
        self.air_provider = AirQualityProvider(settings)

    def run(self, field: FieldMatch, crop: str, latitude: float | None = None,
            longitude: float | None = None) -> AnalysisResult:
        selected, source = self.provider.get(field)
        if len(selected) < 3:
            raise RuntimeError("At least three clear observations are needed")

        regional = pd.read_csv(self.settings.client_dir / "observations.csv")
        regional["field_id"] = pd.to_numeric(regional["field_id"], errors="coerce").astype("Int64")
        if field.field_id not in set(regional["field_id"].dropna().astype(int)):
            observations = pd.concat([regional, selected], ignore_index=True)
        else:
            observations = regional

        fields = json.loads((self.settings.client_dir / "fields.geojson").read_text(encoding="utf-8"))
        known_ids = {int(f["properties"]["field_id"]) for f in fields.get("features", [])}
        if field.field_id not in known_ids:
            years = sorted(pd.to_datetime(selected["date"]).dt.year.unique())
            fields["features"].append({
                "type": "Feature",
                "properties": {"field_id": field.field_id, "name": f"Field {field.field_id}",
                               "seasons": {str(year): crop for year in years}},
                "geometry": field.geometry,
            })

        scripts = self.settings.root / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from ml_engine import score_latest

        as_of = pd.to_datetime(selected["date"]).max()
        scored = score_latest(
            observations, fields, self.settings.model_path, as_of=as_of,
            window_days=45, alert_percentile=self.settings.alert_percentile,
        )
        row = scored[scored["field_id"] == field.field_id]
        if row.empty:
            raise RuntimeError("The selected field could not be scored for its latest observation")
        score = row.sort_values("date").iloc[-1]
        ordered = selected.sort_values("date")
        latest = float(ordered.iloc[-1]["ndvi"])
        previous = float(ordered.iloc[-2]["ndvi"]) if len(ordered) > 1 else None
        latest_date = str(ordered.iloc[-1]["date"])[:10]
        age = (pd.Timestamp.now().normalize() - pd.Timestamp(latest_date)).days
        if field.area_ha < self.settings.min_reliable_area_ha or age > 30:
            confidence = "low"
        elif len(ordered) < 10 or age > 14:
            confidence = "medium"
        else:
            confidence = "high"
        indices: dict[str, float | None] = {}
        changes: dict[str, float | None] = {}
        for name in ("ndvi", "ndre", "ndmi", "evi", "savi"):
            series = pd.to_numeric(ordered.get(name), errors="coerce").dropna()
            indices[name] = float(series.iloc[-1]) if len(series) else None
            changes[name] = float(series.iloc[-1] - series.iloc[-2]) if len(series) > 1 else None

        if latitude is None or longitude is None:
            west, south, east, north = field.bounds
            latitude, longitude = (south + north) / 2, (west + east) / 2
        air_quality = None
        air_error = None
        try:
            air_quality = self.air_provider.get(float(latitude), float(longitude))
        except Exception as exc:
            # A third-party regional context service must never prevent the
            # field-level Sentinel report from being delivered.
            air_error = f"{type(exc).__name__}: {exc}"[:180]
        return AnalysisResult(
            field=field, crop=crop, observations=ordered, source=source,
            latest_date=latest_date, latest_ndvi=latest, previous_ndvi=previous,
            peak_ndvi=float(pd.to_numeric(ordered["ndvi"], errors="coerce").max()),
            change=(latest - previous) if previous is not None else None,
            anomaly_percentile=float(score["ml_risk"]), alert=bool(score["ml_flag"]),
            explanation=str(score["ml_explanation"]), confidence=confidence,
            indices=indices, index_changes=changes, air_quality=air_quality,
            air_quality_error=air_error,
        )
