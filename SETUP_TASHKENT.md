# Tashkent Region: complete preparation and launch

Run every command from the project directory. The boundary download covers the
whole administrative Tashkent Region. The downloaded boundaries are public ML
predictions, not cadastral or ownership records.

## 1. Create the Python environment

```bash
cd /home/hontonoshin/Downloads/ai
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Python 3.11 or 3.12 is recommended.

## 2. Configure secrets and runtime options

```bash
cp -n .env.example .env
nano .env
```

Set `TELEGRAM_BOT_TOKEN`, `CDSE_CLIENT_ID`, and `CDSE_CLIENT_SECRET`. Leave
`AIR_QUALITY_ENABLED=true`. The default Open-Meteo prototype endpoint does not
need a key for permitted non-commercial/evaluation use. Keep its attribution in
the report and check its licence before commercial deployment.

## 3. Download all predicted Tashkent field boundaries

```bash
mkdir -p data/regions/tashkent

python scripts/11_download_ftw_region.py \
  --place "Tashkent Region, Uzbekistan" \
  --output data/regions/tashkent/fields.geojson \
  --year 2025 \
  --min-area-ha 0.3 \
  --max-area-ha 500
```

This remote query may take 30–90 minutes. The script now uses a five-minute HTTP
timeout, retries, backoff and metadata caches. A successful run prints the saved
polygon count. The previously observed result was about 68,000 candidates.

Check the file:

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path("data/regions/tashkent/fields.geojson")
d = json.loads(p.read_text())
print("fields:", len(d["features"]))
print("size MB:", round(p.stat().st_size / 1024 / 1024, 1))
PY
```

## 4. Register full coverage and build the regional reference

All downloaded boundaries are registered for location matching. A spatially
distributed sample of 1,000 fields is used for the historical ML reference;
other matched fields are fetched and cached on demand. This gives whole-region
location coverage without attempting hundreds of thousands of openEO jobs.

```bash
python scripts/09_build_region.py \
  --fields data/regions/tashkent/fields.geojson \
  --region-id tashkent \
  --client tashkent \
  --label "Tashkent Region" \
  --start 2023-01-01 \
  --reference-size 1000 \
  --chunk-size 100 \
  --fetch
```

This retrieves NDVI, NDRE, NDMI, EVI and SAVI and then trains model schema 2.0.
It can take hours and uses Copernicus processing quota. The per-job JSON cache
under `clients/tashkent/_cache/` makes reruns resume-safe.

If fetching stops, run the same command again. Then verify:

```bash
python scripts/01_setup_check.py --client tashkent
python scripts/06_train_ml.py --client tashkent
python scripts/03_anomalies.py --client tashkent
python scripts/04_report.py --client tashkent --language en
```

## 5. Cache a small set of satellite previews

```bash
python scripts/10_cache_satellite_previews.py --region tashkent --limit 20
```

This is enough for a systems test. The bot can request other previews when a
user sends a location. To expand the cache later:

```bash
python scripts/10_cache_satellite_previews.py --region tashkent --workers 4
```

## 6. Upgrade Khorezm to the five-index model

Existing NDVI-only history remains readable. For genuine EVI/SAVI/NDRE/NDMI
history, refetch and retrain:

```bash
python scripts/02_fetch_indices.py \
  --client khorezm --start 2023-01-01 --full-refresh --headless
python scripts/06_train_ml.py --client khorezm
python scripts/03_anomalies.py --client khorezm
python scripts/04_report.py --client khorezm --language en
```

## 7. Start and test the bot

```bash
python run_bot.py
```

In Telegram:

1. Send `/start`.
2. Select Uzbek, Russian or English.
3. Share a Tashkent or Khorezm location.
4. Confirm the satellite image and red field boundary.
5. Select the crop.
6. Receive the five-index chart, ML screening result, regional air-quality
   context and PDF.

Air-quality values (AQI, PM2.5, PM10, CO, NO₂, SO₂, O₃, dust and AOD) come from
the coarse CAMS global model via Open-Meteo. They describe regional conditions,
not the individual field, and they are not used as evidence by the canopy model.

## 8. Weekly refresh

```bash
python scripts/02_fetch_indices.py --client tashkent --headless
python scripts/06_train_ml.py --client tashkent
python scripts/03_anomalies.py --client tashkent
python scripts/04_report.py --client tashkent --language en
```

Do not claim disease, irrigation need, soil chemistry, yield, land ownership or
legal boundaries. The product prioritizes inspection from satellite patterns.
