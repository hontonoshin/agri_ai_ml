"""Start the Telegram bot using long polling."""
from telegram_bot.bot import build_application
from telegram_bot.settings import Settings


if __name__ == "__main__":
    build_application(Settings.load()).run_polling(drop_pending_updates=False)
