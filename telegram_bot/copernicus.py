"""One-field Sentinel-2 retrieval with persistent CSV caching."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd

from .field_lookup import FieldMatch
from .settings import Settings


def _normalise(frame: pd.DataFrame, field_id: int) -> pd.DataFrame:
    frame = frame.copy()
    for name in ("ndvi", "ndre", "ndmi", "evi", "savi", "valid_frac"):
        if name not in frame:
            frame[name] = float("nan")
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame["field_id"] = int(field_id)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
    return frame.dropna(subset=["date", "ndvi"]).sort_values("date").drop_duplicates("date", keep="last")


def _parse_result(payload: dict, field_id: int) -> pd.DataFrame:
    rows = []
    for timestamp, geometries in payload.items():
        try:
            date = pd.Timestamp(timestamp).date().isoformat()
        except Exception:
            continue
        if not geometries:
            continue
        values = geometries[0]
        if not isinstance(values, list):
            values = [values]
        names = ("ndvi", "ndre", "ndmi", "evi", "savi", "valid_frac")
        record = {"field_id": field_id, "date": date}
        try:
            for position, name in enumerate(names):
                record[name] = float(values[position]) if position < len(values) and values[position] is not None else None
        except (TypeError, ValueError):
            continue
        if (record["ndvi"] is not None and -1 <= record["ndvi"] <= 1
                and (record["valid_frac"] is None or record["valid_frac"] >= 0.30)):
            rows.append(record)
    return _normalise(pd.DataFrame(rows), field_id) if rows else pd.DataFrame()


class ObservationProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _regional(self, field_id: int) -> pd.DataFrame:
        path = self.settings.client_dir / "observations.csv"
        frame = pd.read_csv(path)
        block = frame[pd.to_numeric(frame["field_id"], errors="coerce") == int(field_id)]
        return _normalise(block, field_id) if len(block) else pd.DataFrame()

    def _cache_path(self, field_id: int) -> Path:
        return self.settings.field_cache_dir / f"field_{int(field_id)}.csv"

    def get(self, field: FieldMatch) -> tuple[pd.DataFrame, str]:
        regional = self._regional(field.field_id)
        if len(regional):
            return regional, "regional_cache"
        cache = self._cache_path(field.field_id)
        if cache.exists():
            age = dt.datetime.now().timestamp() - cache.stat().st_mtime
            cached = pd.read_csv(cache)
            # Schema-v1 on-demand caches contain only NDVI/NDRE/NDMI. Reusing
            # them would show EVI and SAVI as unavailable even though the new
            # retrieval supports both indices. Refresh these old caches once.
            has_five_indices = all(
                name in cached and pd.to_numeric(cached[name], errors="coerce").notna().any()
                for name in ("ndvi", "ndre", "ndmi", "evi", "savi")
            )
            if age <= self.settings.cache_days * 86400 and has_five_indices:
                return _normalise(cached, field.field_id), "field_cache"
        fresh = self._fetch(field)
        if fresh.empty:
            raise RuntimeError("No sufficiently clear Sentinel-2 observations were returned")
        fresh.to_csv(cache, index=False)
        return fresh, "copernicus_on_demand"

    def _fetch(self, field: FieldMatch) -> pd.DataFrame:
        try:
            import openeo
        except ImportError as exc:
            raise RuntimeError("openEO is not installed; run pip install -r requirements.txt") from exc

        connection = openeo.connect("https://openeo.dataspace.copernicus.eu")
        if self.settings.cdse_client_id and self.settings.cdse_client_secret:
            connection.authenticate_oidc_client_credentials(
                client_id=self.settings.cdse_client_id,
                client_secret=self.settings.cdse_client_secret,
            )
        else:
            connection.authenticate_oidc()

        end = dt.date.today() - dt.timedelta(days=self.settings.latency_days)
        start = end - dt.timedelta(days=365 * self.settings.on_demand_years)
        west, south, east, north = field.bounds
        cube = connection.load_collection(
            "SENTINEL2_L2A",
            spatial_extent={"west": west, "south": south, "east": east, "north": north},
            temporal_extent=[start.isoformat(), end.isoformat()],
            bands=["B02", "B04", "B05", "B08", "B11", "SCL"],
            max_cloud_cover=80,
        ).resample_spatial(resolution=10)
        good = (cube.band("SCL") == 4) | (cube.band("SCL") == 5)
        indices = {
            "ndvi": (cube.band("B08") - cube.band("B04")) / (cube.band("B08") + cube.band("B04")),
            "ndre": (cube.band("B08") - cube.band("B05")) / (cube.band("B08") + cube.band("B05")),
            "ndmi": (cube.band("B08") - cube.band("B11")) / (cube.band("B08") + cube.band("B11")),
            # Sentinel-2 L2A reflectance uses a 0.0001 scale, therefore the
            # EVI/SAVI additive constants are 10000 and 5000 in raw units.
            "evi": 2.5 * (cube.band("B08") - cube.band("B04")) / (
                cube.band("B08") + 6.0 * cube.band("B04")
                - 7.5 * cube.band("B02") + 10000.0
            ),
            "savi": 1.5 * (cube.band("B08") - cube.band("B04")) / (
                cube.band("B08") + cube.band("B04") + 5000.0
            ),
        }
        merged = None
        for name, value in indices.items():
            value = value.mask(~good).add_dimension(name="bands", label=name, type="bands")
            merged = value if merged is None else merged.merge_cubes(value)
        valid = good.add_dimension(name="bands", label="valid", type="bands")
        merged = merged.merge_cubes(valid)
        geometries = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {}, "geometry": field.geometry}
        ]}
        result = merged.aggregate_spatial(geometries=geometries, reducer="mean")
        job = result.execute_batch(out_format="JSON", title=f"field_{field.field_id}_ndvi")
        temporary = self.settings.field_cache_dir / f"field_{field.field_id}.json.part"
        try:
            job.get_results().download_file(str(temporary))
            payload = json.loads(temporary.read_text(encoding="utf-8"))
        finally:
            temporary.unlink(missing_ok=True)
            try:
                job.delete_job()
            except Exception:
                pass
        return _parse_result(payload, field.field_id)
