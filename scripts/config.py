"""Configuration: spectral indices, crop calendars, anomaly rules, scope.

SCOPE — read before changing anything here.
------------------------------------------
Everything in this system is a RELATIVE observation of the crop canopy, derived
from Sentinel-2 surface reflectance. Nothing here measures soil.

What is measured:
    NDVI  canopy vigour / green biomass
    NDRE  red-edge vigour; a *proxy* for canopy nitrogen status at high biomass
    NDMI  canopy water content (SWIR-based)
    EVI   canopy vigour with stronger soil/background and atmosphere correction
    SAVI  canopy vigour adjusted for visible soil background

What is NOT measured, and must never be claimed in any output:
    soil organic carbon, NPK, pH, texture, salinity
    soil moisture, irrigation requirement or scheduling
    yield in absolute units
    disease or pest identification
    land ownership or legal status

Anomalies are statements about where a parcel sits relative to (a) the client's
other parcels of the same crop this week, and (b) that parcel's own history.
They are directions to look, not diagnoses.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# spectral indices
# --------------------------------------------------------------------------- #
# Sentinel-2 L2A bands used. B05/B11 are native 20 m; the cube is resampled to
# 20 m so every index shares one grid (see 02_fetch_indices.py).
BANDS = ["B02", "B04", "B05", "B08", "B11", "SCL"]
RESAMPLE_RESOLUTION = 20
# Copernicus Sentinel-2 L2A reflectance bands are stored with a 0.0001 scale.
# NDVI/NDRE/NDMI cancel this scale. EVI/SAVI contain additive constants, so
# their formulas below explicitly use the corresponding raw-DN constants.
REFLECTANCE_SCALE = 10000.0

INDICES = {
    "ndvi": {
        "formula": "normalized_difference",
        "bands": ("B08", "B04"),
        "range": (-1.0, 1.0),
        "label_en": "Canopy vigour (NDVI)",
        "label_uz": "O'simlik qoplami quvvati (NDVI)",
        "label_ru": "Развитие растительного покрова (NDVI)",
        "means": "green biomass / canopy density",
    },
    "ndre": {
        "formula": "normalized_difference",
        "bands": ("B08", "B05"),
        "range": (-1.0, 1.0),
        "label_en": "Red-edge vigour (NDRE)",
        "label_uz": "Qizil chet quvvati (NDRE)",
        "label_ru": "Красный край (NDRE)",
        "means": "proxy for canopy nitrogen status; not a nutrient measurement",
    },
    "ndmi": {
        "formula": "normalized_difference",
        "bands": ("B08", "B11"),
        "range": (-1.0, 1.0),
        "label_en": "Canopy moisture (NDMI)",
        "label_uz": "O'simlik namligi (NDMI)",
        "label_ru": "Влажность растительного покрова (NDMI)",
        "means": "canopy water content; NOT soil moisture, NOT irrigation need",
    },
    "evi": {
        "formula": "evi",
        "bands": ("B08", "B04", "B02"),
        "range": (-1.5, 1.5),
        "label_en": "Enhanced vegetation (EVI)",
        "label_uz": "Kuchaytirilgan o'simlik indeksi (EVI)",
        "label_ru": "Улучшенный индекс растительности (EVI)",
        "means": "canopy vigour with reduced soil-background and atmospheric sensitivity",
    },
    "savi": {
        "formula": "savi",
        "bands": ("B08", "B04"),
        "range": (-1.5, 1.5),
        "label_en": "Soil-adjusted vegetation (SAVI)",
        "label_uz": "Tuproq foniga moslashtirilgan indeks (SAVI)",
        "label_ru": "Почвенно-корректированный индекс (SAVI)",
        "means": "canopy vigour adjusted for visible soil background; not a soil measurement",
    },
}

# Clear-sky mask: SCL 4 = vegetation, 5 = bare/not-vegetated.
GOOD_SCL = (4, 5)
MAX_CLOUD_COVER = 90
MIN_VALID_FRAC = 0.30  # discard a field-date built from fewer clear pixels than this


# --------------------------------------------------------------------------- #
# crop calendars (day-of-year windows, Uzbekistan)
# --------------------------------------------------------------------------- #
# These are regional defaults for screening only. Override per client in
# clients/<client>/client.json -> "crop_calendar_overrides".
CROP_CALENDARS = {
    "cotton": {
        "label_en": "Cotton",
        "label_uz": "Paxta",
        "label_ru": "Хлопок",
        "emergence": (110, 145),    # late Apr - late May
        "peak": (190, 230),         # early Jul - mid Aug
        "senescence": (240, 285),   # late Aug - mid Oct
        "expected_peak_ndvi": 0.65,
    },
    "wheat": {
        "label_en": "Winter wheat",
        "label_uz": "Kuzgi bug'doy",
        "label_ru": "Озимая пшеница",
        "emergence": (60, 90),      # regrowth after winter
        "peak": (110, 140),
        "senescence": (150, 185),   # harvest ~late Jun
        "expected_peak_ndvi": 0.70,
    },
    "rice": {
        "label_en": "Rice",
        "label_uz": "Sholi",
        "label_ru": "Рис",
        "emergence": (140, 175),
        "peak": (200, 240),
        "senescence": (250, 290),
        "expected_peak_ndvi": 0.70,
    },
    "orchard": {
        "label_en": "Orchard / vineyard",
        "label_uz": "Bog' / uzumzor",
        "label_ru": "Сад / виноградник",
        "emergence": (90, 120),
        "peak": (150, 250),
        "senescence": (270, 310),
        "expected_peak_ndvi": 0.60,
    },
    "other": {
        "label_en": "Other / unspecified",
        "label_uz": "Boshqa",
        "label_ru": "Другое",
        "emergence": (90, 150),
        "peak": (180, 240),
        "senescence": (250, 300),
        "expected_peak_ndvi": 0.60,
    },
}


# --------------------------------------------------------------------------- #
# anomaly rules
# --------------------------------------------------------------------------- #
# MIN_COHORT is the number of same-crop parcels needed before a percentile is
# meaningful. Below it, cohort rules are suppressed rather than guessed.
MIN_COHORT = 8
MIN_COHORT_SCALE = 0.03  # floor on the MAD scale: a very tight cohort must not
                         # turn a trivial difference into a huge z-score

RULES = {
    # Field is in the bottom tail of its same-crop cohort AND materially below
    # the cohort median. Both gates required: a percentile alone flags a fixed
    # share of the cohort by construction, even when every field is healthy.
    # Robust outlier test, not a percentile. A percentile gate flags a fixed
    # share of the cohort by construction: with 20 parcels the bottom decile is
    # two, so a third genuinely failing parcel is silently invisible. The MAD
    # z-score fires on however many parcels actually deviate — and on none when
    # the cohort is uniformly healthy.
    "low_vigour_vs_cohort": {
        "max_z": -2.0,              # robust z vs same-crop cohort
        "max_relative": 0.75,       # and <= 75% of cohort median
        "severity": "high",
    },
    # Sharp drop against the parcel's own recent mean, outside its senescence
    # window (where decline is expected and normal).
    "vigour_drop_vs_self": {
        "window_obs": 3,
        "min_drop": 0.12,           # absolute NDVI
        "severity": "high",
    },
    # Canopy moisture low relative to the same-crop cohort. Reported as canopy
    # moisture. NOT an irrigation instruction.
    "low_canopy_moisture": {
        "max_z": -2.0,
        "max_relative": 0.75,
        "min_ndvi": 0.30,           # only meaningful once there is a canopy
        "severity": "medium",
    },
    # Still bare well past the expected emergence window.
    "late_emergence": {
        "ndvi_below": 0.25,
        "peak_below": 0.30,         # and never reached a canopy at all this season
        "days_after_window": 14,
        "severity": "high",
    },
    # Declining before the expected senescence window opens.
    "early_senescence": {
        "min_drop_from_peak": 0.20,
        "days_before_window": 21,
        "severity": "medium",
    },
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

SCOPE_STATEMENT = {
    "en": (
        "This report describes the crop canopy as observed by Sentinel-2, relative to "
        "your other parcels of the same crop and to each parcel's own history. It does "
        "not measure soil, soil moisture, nutrients or salinity, and does not determine "
        "irrigation requirements, yield, or disease. Flags indicate where to look, not "
        "what is wrong. Confirm on the ground before acting."
    ),
    "uz": (
        "Ushbu hisobot Sentinel-2 orqali kuzatilgan o'simlik qoplamini sizning shu ekin "
        "turidagi boshqa dalalaringiz va har bir dalaning o'z tarixi bilan taqqoslaydi. "
        "U tuproqni, tuproq namligini, oziq moddalarni yoki sho'rlanishni o'lchamaydi; "
        "sug'orish me'yorini, hosildorlikni yoki kasallikni aniqlamaydi. Belgilar nima "
        "noto'g'ri ekanini emas, qayerga qarash kerakligini ko'rsatadi. Chora ko'rishdan "
        "oldin joyida tekshiring."
    ),
    "ru": (
        "Этот отчёт описывает состояние растительного покрова по данным Sentinel-2 "
        "относительно других ваших участков с той же культурой и относительно истории "
        "самого участка. Он не измеряет почву, влажность почвы, питательные вещества "
        "или засоление и не определяет норму полива, урожайность или болезни. Отметки "
        "показывают, куда посмотреть, а не что именно не так. Проверьте на месте."
    ),
}

LANGUAGES = ("uz", "ru", "en")
