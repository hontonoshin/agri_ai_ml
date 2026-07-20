"""Telegram handlers for the location-to-report workflow."""
from __future__ import annotations

import asyncio
import secrets
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .analysis import AnalysisService
from .reporting import field_preview, ndvi_chart, pdf_report, summary
from .regions import RegionRegistry
from .satellite import satellite_preview
from .settings import Settings
from .storage import Store
from .texts import CROPS, LANGUAGES, crop_label, t


def location_keyboard(language: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(t("send_location", language), request_location=True)]],
                               resize_keyboard=True, one_time_keyboard=False)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.application.bot_data["store"]
    language = store.language(update.effective_user.id)
    keyboard = [[InlineKeyboardButton(label, callback_data=f"lang:{code}")] for code, label in LANGUAGES.items()]
    await update.effective_message.reply_text("Tilni tanlang · Выберите язык · Choose language",
                                              reply_markup=InlineKeyboardMarkup(keyboard))
    await update.effective_message.reply_text(t("welcome", language), reply_markup=location_keyboard(language))


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton(label, callback_data=f"lang:{code}")] for code, label in LANGUAGES.items()]
    await update.effective_message.reply_text("Til · Язык · Language", reply_markup=InlineKeyboardMarkup(keyboard))


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    language = query.data.split(":", 1)[1]
    context.application.bot_data["store"].set_language(query.from_user.id, language)
    await query.edit_message_text(t("welcome", language))
    await query.message.reply_text(t("send_location", language), reply_markup=location_keyboard(language))


async def receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.application.bot_data["store"]
    registry: RegionRegistry = context.application.bot_data["regions"]
    language = store.language(update.effective_user.id)
    point = update.effective_message.location
    located = registry.find(point.latitude, point.longitude)
    nearest_distance = None
    if located is None:
        radius = context.application.bot_data["settings"].nearest_field_km
        nearest = registry.nearest(point.latitude, point.longitude, max_distance_km=radius)
        if nearest is None:
            await update.effective_message.reply_text(t("outside", language), reply_markup=location_keyboard(language))
            return
        located, nearest_distance = nearest
    field = located.field
    request_id = secrets.token_hex(5)
    store.create_request(request_id, update.effective_chat.id, update.effective_user.id,
                         point.latitude, point.longitude, language, field.field_id, field.area_ha,
                         region_id=located.region_id, client=located.client)
    settings = context.application.bot_data["settings"].for_client(located.client)
    preview = settings.reports_dir / request_id / "field.png"
    fallback = False
    try:
        await asyncio.to_thread(satellite_preview, settings, field, point.latitude, point.longitude, preview)
    except Exception:
        fallback = True
        await asyncio.to_thread(field_preview, located.index, field, point.latitude, point.longitude, preview)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(t("yes", language), callback_data=f"confirm:yes:{request_id}"),
        InlineKeyboardButton(t("no", language), callback_data=f"confirm:no:{request_id}"),
    ]])
    if nearest_distance is None:
        caption = t("confirm", language, field_id=field.field_id, area=field.area_ha)
    else:
        caption = t("nearest", language, field_id=field.field_id, distance=nearest_distance)
    if fallback:
        caption += "\n\n" + t("preview_fallback", language)
    with preview.open("rb") as image:
        await update.effective_message.reply_photo(image, caption=caption, reply_markup=keyboard)


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, decision, request_id = query.data.split(":", 2)
    store: Store = context.application.bot_data["store"]
    request = store.get(request_id)
    if not request or request["user_id"] != query.from_user.id:
        return
    language = request["language"]
    if decision == "no":
        store.update(request_id, status="cancelled")
        await query.edit_message_caption(caption=t("cancelled", language))
        return
    store.update(request_id, status="awaiting_crop")
    buttons = [InlineKeyboardButton(crop_label(code, language), callback_data=f"crop:{code}:{request_id}") for code in CROPS]
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    await query.message.reply_text(t("choose_crop", language), reply_markup=InlineKeyboardMarkup(rows))


async def crop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, crop, request_id = query.data.split(":", 2)
    store: Store = context.application.bot_data["store"]
    request = store.get(request_id)
    if not request or request["user_id"] != query.from_user.id or crop not in CROPS:
        return
    store.update(request_id, crop=crop, status="queued")
    await query.edit_message_text(t("queued", request["language"]))
    context.application.create_task(process_request(context.application, request_id), name=f"report-{request_id}")


async def process_request(application: Application, request_id: str) -> None:
    store: Store = application.bot_data["store"]
    base_settings: Settings = application.bot_data["settings"]
    request = store.get(request_id)
    try:
        store.update(request_id, status="processing")
        located = application.bot_data["regions"].get(request.get("region_id") or base_settings.client,
                                                       request["field_id"])
        if located is None:
            raise RuntimeError("Field boundary is no longer available")
        field = located.field
        settings = base_settings.for_client(located.client)
        result = await asyncio.to_thread(
            AnalysisService(settings).run, field, request["crop"],
            request["latitude"], request["longitude"],
        )
        directory = settings.reports_dir / request_id
        chart = await asyncio.to_thread(
            ndvi_chart, result, directory / "ndvi.png", request["language"]
        )
        pdf = await asyncio.to_thread(
            pdf_report, result, chart, directory / "report.pdf", request["language"],
            directory / "field.png",
        )
        store.update(request_id, status="complete", data_source=result.source,
                     latest_date=result.latest_date, latest_ndvi=result.latest_ndvi,
                     anomaly_percentile=result.anomaly_percentile, confidence=result.confidence,
                     report_path=str(pdf))
        with chart.open("rb") as image:
            await application.bot.send_photo(request["chat_id"], image, caption=summary(result, request["language"]))
        with pdf.open("rb") as document:
            await application.bot.send_document(request["chat_id"], document, filename=f"field_{field.field_id}_report.pdf")
    except Exception as exc:
        message = str(exc)[:300]
        store.update(request_id, status="failed", error=message)
        await application.bot.send_message(request["chat_id"], t("failed", request["language"], error=message))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.application.bot_data["store"]
    rows = store.latest_for_user(update.effective_user.id, 5)
    language = store.language(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text(t("no_status", language))
        return
    await update.effective_message.reply_text("\n".join(f"#{r['field_id']} · {r['status']} · {r['created_at'][:16]}" for r in rows))


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: Store = context.application.bot_data["store"]
    language = store.language(update.effective_user.id)
    paths = store.delete_user(update.effective_user.id)
    for value in paths:
        path = Path(value)
        if path.is_file() and context.application.bot_data["settings"].reports_dir in path.parents:
            for sibling in path.parent.glob("*"):
                sibling.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
    await update.effective_message.reply_text(t("deleted", language))


def build_application(settings: Settings) -> Application:
    settings.ensure_directories()
    settings.validate_runtime()
    application = Application.builder().token(settings.token).concurrent_updates(4).build()
    application.bot_data.update(settings=settings, store=Store(settings.database_path),
                                regions=RegionRegistry(settings))
    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("delete_my_data", delete_command))
    application.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang:"))
    application.add_handler(CallbackQueryHandler(confirm_callback, pattern=r"^confirm:"))
    application.add_handler(CallbackQueryHandler(crop_callback, pattern=r"^crop:"))
    application.add_handler(MessageHandler(filters.LOCATION, receive_location))
    return application
