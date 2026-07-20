"""Download a regional subset of the global Fields of the World predictions.

The source is a public 2025 ML-derived field-boundary product, not a cadastral
ownership map. The script resolves one administrative boundary with Nominatim,
uses GeoParquet bbox covering columns to avoid downloading the global archive,
and writes the GeoJSON expected by 09_build_region.py.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb


FTW_PARQUET = (
    "s3://us-west-2.opendata.source.coop/tge-labs/ftw-global-data/"
    "predictions/vectors/alpha/results/*.parquet"
)


def sql_string(value: str | Path) -> str:
    """Return a safely quoted DuckDB string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def administrative_boundary(place: str) -> dict:
    query = urllib.parse.urlencode({
        "q": place, "format": "geojson", "polygon_geojson": 1,
        "limit": 1, "addressdetails": 1,
    })
    request = urllib.request.Request(
        "https://nominatim.openstreetmap.org/search?" + query,
        headers={"User-Agent": "UzAgriAI/1.0 regional field preparation"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    features = payload.get("features", [])
    if not features:
        raise RuntimeError(f"Administrative boundary not found for {place!r}")
    geometry = features[0].get("geometry") or {}
    if geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise RuntimeError(f"Search result for {place!r} is not an area polygon")
    return {"type": "FeatureCollection", "features": [features[0]]}


def coordinate_bounds(geometry: dict) -> tuple[float, float, float, float]:
    points = []
    def visit(value):
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
            points.append((float(value[0]), float(value[1])))
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(geometry.get("coordinates", []))
    if not points:
        raise RuntimeError("Administrative boundary has no coordinates")
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def main(place: str, output: str, year: int, min_area_ha: float, max_area_ha: float) -> Path:
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    boundary = administrative_boundary(place)
    west, south, east, north = coordinate_bounds(boundary["features"][0]["geometry"])
    print(f"Administrative area: {place}")
    print(f"Bounding box: {west:.5f}, {south:.5f}, {east:.5f}, {north:.5f}")
    print("Querying public Fields of the World GeoParquet; this can take a while...")

    with tempfile.TemporaryDirectory(prefix="ftw_region_") as temporary:
        boundary_path = Path(temporary) / "boundary.geojson"
        boundary_path.write_text(json.dumps(boundary), encoding="utf-8")
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        connection = duckdb.connect()
        connection.execute("INSTALL spatial; LOAD spatial;")
        connection.execute("INSTALL httpfs; LOAD httpfs;")
        connection.execute("SET s3_region='us-west-2';")
        connection.execute("SET s3_endpoint='s3.us-west-2.amazonaws.com';")
        connection.execute("SET s3_url_style='path';")
        # The full regional scan touches many remote parquet fragments. These
        # retry/cache settings avoid losing a long run to one slow S3 request.
        connection.execute("SET http_timeout=300;")
        connection.execute("SET http_retries=15;")
        connection.execute("SET http_retry_wait_ms=1000;")
        connection.execute("SET http_retry_backoff=2;")
        connection.execute("SET enable_http_metadata_cache=true;")
        connection.execute("SET parquet_metadata_cache=true;")
        connection.execute("SET threads=4;")
        connection.execute("SET enable_progress_bar=true;")
        # Calculate geodesic area after flipping GeoJSON lon/lat into the
        # latitude/longitude order expected by ST_Area_Spheroid.
        # DuckDB table functions do not bind positional parameters in the
        # same order as ordinary scalar expressions. Use safely quoted file
        # literals so read_parquet cannot receive the date parameter.
        sql = f"""
        COPY (
          WITH boundary AS (
            SELECT geom FROM ST_Read({sql_string(boundary_path)}) LIMIT 1
          ), candidates AS (
            SELECT geometry, bbox
            FROM read_parquet({sql_string(FTW_PARQUET)})
            WHERE label = 'field'
              AND time = TIMESTAMP '{int(year)}-01-01'
              AND struct_extract(bbox, 'xmax') >= {float(west)}
              AND struct_extract(bbox, 'xmin') <= {float(east)}
              AND struct_extract(bbox, 'ymax') >= {float(south)}
              AND struct_extract(bbox, 'ymin') <= {float(north)}
          ), selected AS (
            SELECT c.geometry,
                   ST_Area_Spheroid(ST_FlipCoordinates(c.geometry)) / 10000.0 AS area_ha
            FROM candidates c, boundary b
            WHERE ST_Intersects(c.geometry, b.geom)
          )
          SELECT row_number() OVER ()::BIGINT AS field_id,
                 round(area_ha, 4) AS area_ha,
                 'FTW 2025 ML prediction; not cadastral' AS boundary_source,
                 geometry
          FROM selected
          WHERE area_ha BETWEEN {float(min_area_ha)} AND {float(max_area_ha)}
        ) TO {sql_string(partial)} WITH (FORMAT GDAL, DRIVER 'GeoJSON')
        """
        connection.execute(sql)
        connection.close()
        payload = json.loads(partial.read_text(encoding="utf-8"))
        count = len(payload.get("features", []))
        if not count:
            partial.unlink(missing_ok=True)
            raise RuntimeError("The regional query returned no field polygons")
        # GDAL may emit field_id as a string depending on build; normalize it.
        for number, feature in enumerate(payload["features"], 1):
            props = feature.setdefault("properties", {})
            props["field_id"] = int(props.get("field_id") or number)
            props["area_ha"] = float(props.get("area_ha") or 0.0)
        partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        partial.replace(destination)
    print(f"Saved {count:,} predicted fields to {destination}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--place", required=True,
                        help='administrative place, e.g. "Tashkent Region, Uzbekistan"')
    parser.add_argument("--output", required=True)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--min-area-ha", type=float, default=0.3)
    parser.add_argument("--max-area-ha", type=float, default=500.0)
    args = parser.parse_args()
    main(args.place, args.output, args.year, args.min_area_ha, args.max_area_ha)
