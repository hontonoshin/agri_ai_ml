"""Create Telegram-ready previews, NDVI charts and concise PDF reports."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/agri_ai_matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.dates import DateFormatter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from PIL import Image as PILImage

from .analysis import AnalysisResult
from .air_quality import european_aqi_category
from .field_lookup import FieldIndex, FieldMatch, outer_rings
from .texts import crop_label


def _plot_geometry(axis, geometry: dict, color: str, width: float, fill: str | None = None):
    for ring in outer_rings(geometry):
        if not ring:
            continue
        xs = [point[0] for point in ring]
        ys = [point[1] for point in ring]
        axis.plot(xs, ys, color=color, linewidth=width)
        if fill:
            axis.fill(xs, ys, color=fill, alpha=0.18)


def field_preview(index: FieldIndex, field: FieldMatch, lat: float, lon: float, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(7, 5), dpi=150)
    for nearby in index.nearby(field):
        _plot_geometry(axis, nearby.geometry, "#a7adb4", 0.7)
    _plot_geometry(axis, field.geometry, "#e53935", 2.4, "#e53935")
    axis.scatter([lon], [lat], color="#1565c0", edgecolors="white", s=55, zorder=5)
    west, south, east, north = field.bounds
    pad_x = max((east - west) * 0.55, 0.002)
    pad_y = max((north - south) * 0.55, 0.002)
    axis.set_xlim(min(west, lon) - pad_x, max(east, lon) + pad_x)
    axis.set_ylim(min(south, lat) - pad_y, max(north, lat) + pad_y)
    axis.set_aspect("equal", adjustable="box")
    axis.set_title(f"Detected field #{field.field_id} · {field.area_ha:.2f} ha")
    axis.set_xlabel("Longitude")
    axis.set_ylabel("Latitude")
    axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return path


def ndvi_chart(result: AnalysisResult, path: Path, language: str = "en") -> Path:
    """Plot all available vegetation indices in two readable panels."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = result.observations.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    figure, axes = plt.subplots(2, 1, figsize=(8.2, 5.5), dpi=160, sharex=True)
    groups = [
        (("ndvi", "#2e7d32"), ("evi", "#1565c0"), ("savi", "#8d6e63")),
        (("ndre", "#ad1457"), ("ndmi", "#00838f")),
    ]
    for axis, group in zip(axes, groups):
        plotted = False
        for name, color in group:
            if name not in frame or pd.to_numeric(frame[name], errors="coerce").notna().sum() == 0:
                continue
            values = pd.to_numeric(frame[name], errors="coerce")
            marker_step = max(len(frame) // 36, 1)
            axis.plot(frame["date"], values, color=color, marker="o", markersize=2.0,
                      markevery=marker_step, linewidth=1.3, label=name.upper())
            plotted = True
        axis.axhline(0, color="#777", linewidth=0.7)
        axis.set_ylim(-0.25, 1.15)
        axis.set_ylabel({"uz": "Indeks qiymati", "ru": "Значение индекса"}.get(language, "Index value"))
        axis.grid(alpha=0.22)
        if plotted:
            axis.legend(loc="best", ncol=3, fontsize=8)
    axes[0].set_title({
        "uz": "Sentinel-2 o‘simlik indekslari tarixi",
        "ru": "История индексов растительности Sentinel-2",
    }.get(language, "Sentinel-2 vegetation-index history"))
    axes[-1].xaxis.set_major_formatter(DateFormatter("%Y-%m"))
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight")
    plt.close(figure)
    return path


def _language_text(language: str) -> dict[str, str]:
    texts = {
        "uz": {"title": "Sun’iy yo‘ldosh dala hisoboti", "field": "Dala", "crop": "Ekin",
               "date": "So‘nggi tasvir", "ndvi": "So‘nggi NDVI", "change": "Oxirgi o‘zgarish",
               "risk": "Noodatiylik percentili", "confidence": "Ishonchlilik", "result": "Natija",
               "alert": "Tekshirish tavsiya etiladi", "normal": "Keskin noodatiy signal topilmadi",
               "air": "Mintaqaviy havo sifati", "air_unavailable": "Havo sifati ma’lumoti hozir mavjud emas",
               "aqi": "AQI (Yevropa shkalasi)", "particles": "Zarrachalar", "gases": "Gazlar",
               "satellite": "Tasdiqlangan dala va sun’iy yo‘ldosh tasviri",
               "satellite_note": "Qizil chiziq - foydalanuvchi tasdiqlagan dala chegarasi.",
               "history": "O‘simlik indekslari tarixi", "factors": "Modelning asosiy omillari",
               "note": "Bu skrining natijasi. U kasallik yoki sug‘orish muammosiga tashxis qo‘ymaydi.",
               "ranking": "Percentil tarixiy shakllarga nisbatan reyting, zarar ehtimoli emas.",
               "air_note": "CAMS/Open-Meteo, taxminan 45 km mintaqaviy model; dala o‘lchovi emas."},
        "ru": {"title": "Спутниковый отчёт о поле", "field": "Поле", "crop": "Культура",
               "date": "Последний снимок", "ndvi": "Последний NDVI", "change": "Последнее изменение",
               "risk": "Процентиль необычности", "confidence": "Надёжность", "result": "Результат",
               "alert": "Рекомендуется осмотр", "normal": "Резкого необычного сигнала не обнаружено",
               "air": "Региональное качество воздуха", "air_unavailable": "Данные о воздухе сейчас недоступны",
               "aqi": "AQI (европейская шкала)", "particles": "Частицы", "gases": "Газы",
               "satellite": "Подтверждённое поле и спутниковый снимок",
               "satellite_note": "Красная линия - граница поля, подтверждённая пользователем.",
               "history": "История индексов растительности", "factors": "Основные факторы модели",
               "note": "Это скрининг, а не диагноз болезни или проблемы орошения.",
               "ranking": "Процентиль — рейтинг по истории, а не вероятность ущерба.",
               "air_note": "CAMS/Open-Meteo, региональная модель около 45 км; не измерение поля."},
        "en": {"title": "Satellite field report", "field": "Field", "crop": "Crop",
               "date": "Latest image", "ndvi": "Latest NDVI", "change": "Latest change",
               "risk": "Anomaly percentile", "confidence": "Confidence", "result": "Result",
               "alert": "Field inspection recommended", "normal": "No strong unusual signal detected",
               "air": "Regional air quality", "air_unavailable": "Air-quality context is currently unavailable",
               "aqi": "AQI (European scale)", "particles": "Particles", "gases": "Gases",
               "satellite": "Confirmed field and satellite image",
               "satellite_note": "The red line is the field boundary confirmed by the user.",
               "history": "Vegetation-index history", "factors": "Main model factors",
               "note": "This is screening evidence, not a diagnosis of disease or irrigation problems.",
               "ranking": "The percentile ranks historical patterns; it is not a probability of damage.",
               "air_note": "CAMS via Open-Meteo, regional model about 45 km; not a field measurement."},
    }
    return texts.get(language, texts["en"])


def summary(result: AnalysisResult, language: str) -> str:
    tx = _language_text(language)
    status = tx["alert"] if result.alert else tx["normal"]
    change = "—" if result.change is None else f"{result.change:+.3f}"
    index_line = " · ".join(
        f"{name.upper()} {_format(value, 3)}"
        for name, value in result.indices.items()
    )
    if result.air_quality:
        air = result.air_quality
        air_line = (
            f"{tx['air']}: AQI {_format(air.european_aqi, 0)} · "
            f"PM2.5 {_format(air.pm2_5, 1)} · PM10 {_format(air.pm10, 1)} µg/m³"
        )
    else:
        air_line = tx["air_unavailable"]
    return (
        f"{status}\n"
        f"{tx['field']}: #{result.field.field_id} · {result.field.area_ha:.2f} ha\n"
        f"{tx['crop']}: {crop_label(result.crop, language)}\n"
        f"{tx['date']}: {result.latest_date}\n"
        f"{index_line}\n"
        f"{tx['risk']}: {result.anomaly_percentile * 100:.1f}%\n"
        f"{tx['confidence']}: {_confidence_label(result.confidence, language)}\n\n"
        f"{air_line}\n\n{tx['note']} {tx['ranking']}"
    )


def _format(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _pdf_format(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _confidence_label(value: str, language: str) -> str:
    labels = {
        "uz": {"high": "yuqori", "medium": "o‘rta", "low": "past"},
        "ru": {"high": "высокая", "medium": "средняя", "low": "низкая"},
    }
    return labels.get(language, {}).get(value, value)


def _aqi_category_label(value: str, language: str) -> str:
    labels = {
        "uz": {"good": "yaxshi", "fair": "qoniqarli", "moderate": "o‘rtacha",
               "poor": "yomon", "very poor": "juda yomon", "extremely poor": "o‘ta yomon",
               "unknown": "noma’lum"},
        "ru": {"good": "хорошо", "fair": "приемлемо", "moderate": "умеренно",
               "poor": "плохо", "very poor": "очень плохо", "extremely poor": "крайне плохо",
               "unknown": "неизвестно"},
    }
    return labels.get(language, {}).get(value, value)


def _translated_explanation(value: str, language: str) -> str:
    translations = {
        "uz": {
            "current canopy vigour": "joriy o‘simlik qoplami quvvati",
            "current red-edge vigour": "joriy qizil-chekka indeksi",
            "current canopy moisture": "joriy o‘simlik qoplami namligi",
            "current enhanced vegetation": "joriy EVI holati",
            "current soil-background-adjusted vegetation": "joriy SAVI holati",
            "seasonal timing": "mavsumiy vaqt",
            "recent NDVI change": "so‘nggi NDVI o‘zgarishi",
            "two-observation NDVI change": "ikki kuzatuvdagi NDVI o‘zgarishi",
            "recent NDVI baseline": "yaqindagi NDVI bazaviy darajasi",
            "change from the earlier seasonal peak": "oldingi mavsumiy cho‘qqidan o‘zgarish",
            "NDVI position within the crop cohort": "ekin guruhidagi NDVI o‘rni",
            "NDRE position within the crop cohort": "ekin guruhidagi NDRE o‘rni",
            "NDMI position within the crop cohort": "ekin guruhidagi NDMI o‘rni",
            "EVI position within the crop cohort": "ekin guruhidagi EVI o‘rni",
            "SAVI position within the crop cohort": "ekin guruhidagi SAVI o‘rni",
            "recent NDRE change": "so‘nggi NDRE o‘zgarishi",
            "recent NDMI change": "so‘nggi NDMI o‘zgarishi",
            "recent EVI change": "so‘nggi EVI o‘zgarishi",
            "recent SAVI change": "so‘nggi SAVI o‘zgarishi",
        },
        "ru": {
            "current canopy vigour": "текущее развитие растительного покрова",
            "current red-edge vigour": "текущий индекс красного края",
            "current canopy moisture": "текущая влажность растительного покрова",
            "current enhanced vegetation": "текущее состояние EVI",
            "current soil-background-adjusted vegetation": "текущее состояние SAVI",
            "seasonal timing": "сезонное время",
            "recent NDVI change": "недавнее изменение NDVI",
            "two-observation NDVI change": "изменение NDVI за два наблюдения",
            "recent NDVI baseline": "недавний базовый уровень NDVI",
            "change from the earlier seasonal peak": "изменение от предыдущего сезонного пика",
            "NDVI position within the crop cohort": "позиция NDVI в группе культуры",
            "NDRE position within the crop cohort": "позиция NDRE в группе культуры",
            "NDMI position within the crop cohort": "позиция NDMI в группе культуры",
            "EVI position within the crop cohort": "позиция EVI в группе культуры",
            "SAVI position within the crop cohort": "позиция SAVI в группе культуры",
            "recent NDRE change": "недавнее изменение NDRE",
            "recent NDMI change": "недавнее изменение NDMI",
            "recent EVI change": "недавнее изменение EVI",
            "recent SAVI change": "недавнее изменение SAVI",
        },
    }
    mapping = translations.get(language, {})
    return ", ".join(mapping.get(part.strip(), part.strip()) for part in value.split(","))


def _pdf_fonts() -> tuple[str, str]:
    pairs = [
        ("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
         "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf"),
    ]
    for regular_path, bold_path in pairs:
        if Path(regular_path).exists() and Path(bold_path).exists():
            if "AgriSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("AgriSans", regular_path))
                pdfmetrics.registerFont(TTFont("AgriSansBold", bold_path))
            return "AgriSans", "AgriSansBold"
    return "Helvetica", "Helvetica-Bold"


def _scaled_image(path: Path, max_width: float, max_height: float) -> Image:
    with PILImage.open(path) as source:
        width, height = source.size
    scale = min(max_width / width, max_height / height)
    return Image(str(path), width=width * scale, height=height * scale)


def _index_appendix(language: str, heading: ParagraphStyle, body: ParagraphStyle,
                    small: ParagraphStyle, font: str, bold: str) -> list:
    content = {
        "uz": {
            "title": "Ilova: o‘simlik indekslarini qanday talqin qilish kerak",
            "intro": (
                "Quyidagi oraliqlar umumiy skrining qo‘llanmasidir. Ular sog‘lom yoki nosog‘lom "
                "ekin uchun universal chegara emas. Ekin turi, o‘sish bosqichi, tuproq foni, tasvir "
                "sifati va shu dalaning oldingi qiymatlari doimo hisobga olinishi kerak."
            ),
            "headers": ["Indeks", "Taxminiy oraliqlar", "Nimani ko‘rsatadi", "Qachon tekshirish kerak"],
            "rows": [
                ["NDVI", "0.00-0.20: yalang‘och/siyrak; 0.20-0.40: rivojlanayotgan; 0.40-0.60: o‘rtacha; 0.60-1.00: zich",
                 "Yashil biomassa va o‘simlik qoplami zichligi.",
                 "Ikki aniq kuzatuvda tez pasayish, mavsumga mos kelmaslik yoki shu ekin guruhidan ancha past bo‘lish."],
                ["NDRE", "0.00-0.10: siyrak; 0.10-0.30: rivojlanayotgan; 0.30-0.50: faol; 0.50-1.00: juda zich",
                 "Zich qoplamda xlorofill/red-edge faolligi; azotning bevosita o‘lchovi emas.",
                 "NDVI hali yuqori bo‘lsa ham NDRE ning barqaror pasayishi yoki bir xil ekin dalalaridan keskin farq."],
                ["NDMI", "Manfiy: quruq/yashil bo‘lmagan fon; 0.00-0.20: past; 0.20-0.40: o‘rtacha; 0.40-1.00: yuqori",
                 "O‘simlik qoplamidagi suv miqdoriga sezgir; tuproq namligi emas.",
                 "NDMI ketma-ket pasayib, NDVI/EVI ham tushsa. Sug‘orish qaroridan oldin joyida tekshirish shart."],
                ["EVI", "0.00-0.20: siyrak; 0.20-0.40: o‘rtacha; 0.40-0.70: kuchli; 0.70 dan yuqori: juda zich/sifatni tekshiring",
                 "Atmosfera va tuproq foniga NDVI dan kamroq sezgir o‘simlik faolligi.",
                 "Mavsumdan tashqari keskin pasayish, takroriy past qiymat yoki NDVI/NDRE bilan nomuvofiqlik."],
                ["SAVI", "0.00-0.20: yalang‘och/siyrak; 0.20-0.40: rivojlanayotgan; 0.40-0.70: yaxshi qoplama; 0.70 dan yuqori: zich",
                 "Tuproq foni ko‘rinadigan dalalar uchun moslashtirilgan yashil qoplama ko‘rsatkichi.",
                 "Rivojlanish davrida kutilgan ko‘tarilish bo‘lmasa yoki NDVI/EVI bilan birga tez pasaysa."],
            ],
            "priority": (
                "Eng kuchli xavotir signali bitta absolut son emas: bir necha aniq tasvirda davom etadigan "
                "o‘zgarish, shu ekin va mavsum bosqichidagi dalalardan sezilarli farq hamda bir nechta "
                "indeksning bir yo‘nalishda yomonlashishidir."
            ),
            "note": "Bu jadval ma’lumot uchun. U kasallik, oziqa yetishmasligi yoki sug‘orish ehtiyojini aniqlamaydi.",
        },
        "ru": {
            "title": "Приложение: как интерпретировать индексы растительности",
            "intro": (
                "Диапазоны ниже являются общей подсказкой для скрининга, а не универсальными границами "
                "здоровой культуры. Всегда учитывайте культуру, фазу роста, почвенный фон, качество снимка "
                "и собственную историю поля."
            ),
            "headers": ["Индекс", "Примерные диапазоны", "Что показывает", "Когда проверить поле"],
            "rows": [
                ["NDVI", "0.00-0.20: голая/редкая растительность; 0.20-0.40: развитие; 0.40-0.60: средняя; 0.60-1.00: густая",
                 "Зелёная биомасса и плотность растительного покрова.",
                 "Быстрое падение за два ясных наблюдения, несоответствие сезону или заметно ниже аналогичной культуры."],
                ["NDRE", "0.00-0.10: редко; 0.10-0.30: развитие; 0.30-0.50: активно; 0.50-1.00: очень густо",
                 "Активность хлорофилла/red-edge в густом покрове; не прямое измерение азота.",
                 "Устойчивое снижение при ещё высоком NDVI или резкое отличие от полей той же культуры."],
                ["NDMI", "Отрицательное: сухой/нерастительный фон; 0.00-0.20: низко; 0.20-0.40: средне; 0.40-1.00: высоко",
                 "Чувствителен к воде в растительном покрове; это не влажность почвы.",
                 "Последовательное падение NDMI вместе с NDVI/EVI. Перед решением о поливе обязательна проверка на месте."],
                ["EVI", "0.00-0.20: редко; 0.20-0.40: средне; 0.40-0.70: активно; выше 0.70: очень густо/проверьте качество",
                 "Активность растительности с меньшей чувствительностью к атмосфере и почвенному фону.",
                 "Резкое несезонное падение, повторно низкие значения или несогласованность с NDVI/NDRE."],
                ["SAVI", "0.00-0.20: голо/редко; 0.20-0.40: развитие; 0.40-0.70: хороший покров; выше 0.70: густой",
                 "Зелёный покров с поправкой на видимый почвенный фон.",
                 "Нет ожидаемого роста в период развития или быстрое снижение вместе с NDVI/EVI."],
            ],
            "priority": (
                "Наиболее важен не один абсолютный порог, а устойчивое изменение на нескольких ясных снимках, "
                "отличие от полей той же культуры и фазы, а также ухудшение нескольких индексов одновременно."
            ),
            "note": "Таблица носит информационный характер и не диагностирует болезнь, дефицит питания или потребность в поливе.",
        },
        "en": {
            "title": "Appendix: how to interpret vegetation indices",
            "intro": (
                "The ranges below are general screening guidance, not universal healthy-crop thresholds. "
                "Always consider crop type, growth stage, soil background, image quality and the field’s own history."
            ),
            "headers": ["Index", "Approximate ranges", "What it indicates", "When to inspect"],
            "rows": [
                ["NDVI", "0.00-0.20: bare/sparse; 0.20-0.40: developing; 0.40-0.60: moderate; 0.60-1.00: dense",
                 "Green biomass and canopy density.",
                 "A rapid fall across two clear observations, an out-of-season pattern, or much lower than the same-crop cohort."],
                ["NDRE", "0.00-0.10: sparse; 0.10-0.30: developing; 0.30-0.50: active; 0.50-1.00: very dense",
                 "Red-edge/chlorophyll activity in dense canopy; not a direct nitrogen measurement.",
                 "A sustained fall while NDVI remains high, or a strong difference from fields of the same crop."],
                ["NDMI", "Negative: dry/non-vegetated background; 0.00-0.20: low; 0.20-0.40: moderate; 0.40-1.00: high",
                 "Sensitive to water in the canopy; not soil moisture.",
                 "A continuing NDMI decline together with NDVI/EVI decline. Inspect before making an irrigation decision."],
                ["EVI", "0.00-0.20: sparse; 0.20-0.40: moderate; 0.40-0.70: vigorous; above 0.70: very dense/check quality",
                 "Vegetation activity with reduced atmospheric and soil-background sensitivity.",
                 "A sharp out-of-season fall, repeatedly low values, or disagreement with NDVI/NDRE."],
                ["SAVI", "0.00-0.20: bare/sparse; 0.20-0.40: developing; 0.40-0.70: good cover; above 0.70: dense",
                 "Green cover adjusted for visible soil background.",
                 "No expected increase during growth, or a rapid decline together with NDVI/EVI."],
            ],
            "priority": (
                "The strongest concern signal is not one absolute number: it is a persistent change across several "
                "clear images, a strong difference from fields at the same crop stage, and several indices worsening together."
            ),
            "note": "This table is informational and does not diagnose disease, nutrient deficiency or irrigation need.",
        },
    }[language if language in ("uz", "ru", "en") else "en"]

    cell = ParagraphStyle("appendix_cell", parent=small, fontName=font, fontSize=7.2,
                          leading=9, textColor=colors.HexColor("#202722"))
    cell_bold = ParagraphStyle("appendix_cell_bold", parent=cell, fontName=bold)
    cell_header = ParagraphStyle("appendix_cell_header", parent=cell_bold,
                                 textColor=colors.white)
    data = [[Paragraph(text, cell_header) for text in content["headers"]]]
    for row in content["rows"]:
        data.append([Paragraph(value, cell_bold if position == 0 else cell)
                     for position, value in enumerate(row)])
    table = Table(data, colWidths=[17*mm, 45*mm, 46*mm, 52*mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#275a42")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f6faf7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#f6faf7"), colors.HexColor("#eaf3ed")]),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9bb1a3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [
        PageBreak(), Paragraph(content["title"], heading), Spacer(1, 2*mm),
        Paragraph(content["intro"], body), Spacer(1, 4*mm), table, Spacer(1, 4*mm),
        Paragraph(content["priority"], body), Spacer(1, 3*mm),
        Paragraph(content["note"], small),
    ]


def pdf_report(result: AnalysisResult, chart_path: Path, path: Path, language: str,
               satellite_path: Path | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    font, bold = _pdf_fonts()
    tx = _language_text(language)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title2", parent=styles["Title"], fontName=bold, fontSize=18,
                           leading=22, textColor=colors.HexColor("#173d2b"), alignment=TA_CENTER)
    heading = ParagraphStyle("heading2", parent=styles["Heading2"], fontName=bold, fontSize=12,
                             leading=15, textColor=colors.HexColor("#214f3a"), spaceAfter=4)
    body = ParagraphStyle("body2", parent=styles["BodyText"], fontName=font, fontSize=9,
                          leading=13, alignment=TA_LEFT)
    small = ParagraphStyle("small2", parent=body, fontSize=7.8, leading=10,
                           textColor=colors.HexColor("#58645d"))
    status_style = ParagraphStyle("status2", parent=body, fontName=bold, fontSize=11,
                                  leading=14, alignment=TA_CENTER,
                                  textColor=colors.HexColor("#a12b2b" if result.alert else "#246b45"))
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm)
    status = tx["alert"] if result.alert else tx["normal"]
    rows = [
        [tx["field"], f"#{result.field.field_id} | {result.field.area_ha:.2f} ha"],
        [tx["crop"], crop_label(result.crop, language)], [tx["date"], result.latest_date],
        *[[name.upper(), f"{_pdf_format(result.indices.get(name), 3)}  "
                         f"(change {_pdf_format(result.index_changes.get(name), 3)})"]
          for name in ("ndvi", "ndre", "ndmi", "evi", "savi")],
        [tx["risk"], f"{result.anomaly_percentile*100:.1f}%"],
        [tx["confidence"], _confidence_label(result.confidence, language)],
    ]
    table = Table(rows, colWidths=[55*mm, 105*mm])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), font, 9.5), ("FONT", (0, 0), (0, -1), bold, 9.5),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef6ee")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b7c4b7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story = [Paragraph(tx["title"], title), Spacer(1, 2*mm), Paragraph(status, status_style),
             Spacer(1, 4*mm)]
    if satellite_path and satellite_path.exists():
        story.extend([
            Paragraph(tx["satellite"], heading),
            _scaled_image(satellite_path, 160*mm, 110*mm),
            Spacer(1, 1.5*mm), Paragraph(tx["satellite_note"], small), Spacer(1, 4*mm),
        ])
    story.extend([table, PageBreak(), Paragraph(tx["history"], heading),
                  _scaled_image(chart_path, 160*mm, 104*mm), Spacer(1, 4*mm),
                  Paragraph(tx["factors"], heading),
                  Paragraph(_translated_explanation(result.explanation, language), body),
                  Spacer(1, 4*mm)])
    if result.air_quality:
        air = result.air_quality
        category = _aqi_category_label(european_aqi_category(air.european_aqi), language)
        air_rows = [
            [tx["aqi"], f"{_pdf_format(air.european_aqi, 0)} ({category})"],
            [tx["particles"], f"PM2.5 {_pdf_format(air.pm2_5, 1)} | PM10 {_pdf_format(air.pm10, 1)} | dust {_pdf_format(air.dust, 1)} ug/m3"],
            [tx["gases"], f"NO2 {_pdf_format(air.nitrogen_dioxide, 1)} | O3 {_pdf_format(air.ozone, 1)} | SO2 {_pdf_format(air.sulphur_dioxide, 1)} | CO {_pdf_format(air.carbon_monoxide, 1)} ug/m3"],
            ["AOD", _pdf_format(air.aerosol_optical_depth, 3)],
        ]
        air_table = Table(air_rows, colWidths=[42*mm, 118*mm])
        air_table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), font, 8.5), ("FONT", (0, 0), (0, -1), bold, 8.5),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef4fb")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b5c6d8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([Paragraph(tx["air"], heading), Spacer(1, 1*mm), air_table,
                      Spacer(1, 2*mm), Paragraph(tx["air_note"], body), Spacer(1, 3*mm)])
    else:
        story.extend([Paragraph(tx["air_unavailable"], body), Spacer(1, 3*mm)])
    story.append(Paragraph(tx["note"] + " " + tx["ranking"], body))
    story.extend(_index_appendix(language, heading, body, small, font, bold))
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.setFillColor(colors.HexColor("#6f7772"))
        canvas.drawString(18*mm, 9*mm, "UzAgriAI | Sentinel-2 / Copernicus")
        canvas.drawRightString(A4[0] - 18*mm, 9*mm, f"{document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
