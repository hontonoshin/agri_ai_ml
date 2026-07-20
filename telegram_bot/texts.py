"""Short user-facing messages in Uzbek, Russian and English."""
from __future__ import annotations

LANGUAGES = {"uz": "O‘zbekcha", "ru": "Русский", "en": "English"}
CROPS = {
    "cotton": {"uz": "Paxta", "ru": "Хлопок", "en": "Cotton"},
    "wheat": {"uz": "Bug‘doy", "ru": "Пшеница", "en": "Wheat"},
    "rice": {"uz": "Guruch", "ru": "Рис", "en": "Rice"},
    "grapes": {"uz": "Uzum", "ru": "Виноград", "en": "Grapes"},
    "tomato": {"uz": "Pomidor", "ru": "Томат", "en": "Tomato"},
    "potato": {"uz": "Kartoshka", "ru": "Картофель", "en": "Potato"},
    "vegetables": {"uz": "Sabzavot", "ru": "Овощи", "en": "Vegetables"},
    "orchard": {"uz": "Bog‘", "ru": "Сад", "en": "Orchard"},
    "other": {"uz": "Boshqa", "ru": "Другое", "en": "Other"},
}

MESSAGES = {
    "welcome": {
        "uz": "Dala holatini tekshirish uchun quyidagi tugma orqali joylashuvingizni yuboring.",
        "ru": "Отправьте местоположение кнопкой ниже, чтобы проверить состояние поля.",
        "en": "Send your location with the button below to check the field condition.",
    },
    "send_location": {"uz": "📍 Joylashuvni yuborish", "ru": "📍 Отправить геопозицию", "en": "📍 Send location"},
    "outside": {
        "uz": "Bu nuqta xaritadagi qishloq xo‘jaligi dalasiga tushmadi. Bu turar joy, yo‘l yoki xaritaga kiritilmagan yer bo‘lishi mumkin. Yaqin dala ham topilmadi; boshqa joy yuboring.",
        "ru": "Точка не попала в нанесённое сельскохозяйственное поле. Это может быть жилая зона, дорога или ещё не нанесённая территория. Ближайшее поле не найдено; отправьте другую точку.",
        "en": "The point is outside mapped agricultural fields. It may be residential, a road, or unmapped land. No nearby field was found; send another location.",
    },
    "nearest": {
        "uz": "Bu nuqta dala ichida emas. Eng yaqin xaritadagi dala #{field_id}, {distance:.2f} km uzoqlikda. Qizil chegara sizning dalangizmi?",
        "ru": "Точка находится вне поля. Ближайшее нанесённое поле #{field_id} — в {distance:.2f} км. Красная граница соответствует вашему полю?",
        "en": "This point is outside a mapped field. The nearest field is #{field_id}, {distance:.2f} km away. Does the red boundary match your field?",
    },
    "confirm": {
        "uz": "Topilgan dala: #{field_id}, taxminan {area:.2f} ga. Qizil chegara sizning dalangizmi?",
        "ru": "Найдено поле #{field_id}, примерно {area:.2f} га. Красная граница соответствует вашему полю?",
        "en": "Detected field #{field_id}, about {area:.2f} ha. Does the red boundary match your field?",
    },
    "yes": {"uz": "Ha", "ru": "Да", "en": "Yes"},
    "no": {"uz": "Yo‘q", "ru": "Нет", "en": "No"},
    "choose_crop": {"uz": "Ekin turini tanlang:", "ru": "Выберите культуру:", "en": "Choose the crop:"},
    "queued": {
        "uz": "So‘rov qabul qilindi. Bulutsiz tasvirlarni tekshirish bir necha daqiqa olishi mumkin.",
        "ru": "Запрос принят. Проверка безоблачных снимков может занять несколько минут.",
        "en": "Request accepted. Checking clear satellite observations may take several minutes.",
    },
    "failed": {
        "uz": "Hisobotni yaratib bo‘lmadi: {error}",
        "ru": "Не удалось создать отчёт: {error}",
        "en": "The report could not be created: {error}",
    },
    "cancelled": {"uz": "Bekor qilindi. Yangi joylashuv yuborishingiz mumkin.", "ru": "Отменено. Можно отправить новую точку.", "en": "Cancelled. You can send another location."},
    "deleted": {"uz": "So‘rovlaringiz va hisobotlaringiz o‘chirildi.", "ru": "Ваши запросы и отчёты удалены.", "en": "Your requests and reports were deleted."},
    "no_status": {"uz": "Hali so‘rov yo‘q.", "ru": "Запросов пока нет.", "en": "No requests yet."},
    "preview_fallback": {
        "uz": "Sun’iy yo‘ldosh rasmi hozir olinmadi; chegara sxemasi ko‘rsatildi.",
        "ru": "Спутниковый снимок сейчас недоступен; показана схема границы.",
        "en": "The satellite preview was unavailable, so a boundary diagram is shown.",
    },
}


def t(key: str, language: str, **values) -> str:
    choices = MESSAGES[key]
    return choices.get(language, choices["en"]).format(**values)


def crop_label(crop: str, language: str) -> str:
    return CROPS.get(crop, CROPS["other"]).get(language, crop)
