"""Entry point — Gemini Pixel Verify Bot."""

import asyncio
import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.config import BOT_TOKEN
from bot.database import ensure_indexes, ping
from bot.handlers import cmd_admin, cmd_start, handle_menu, handle_text


async def _post_init(app: Application) -> None:  # type: ignore[type-arg]
    """Run once after the bot is initialised but before polling starts."""
    if not await ping():
        logging.getLogger(__name__).warning(
            "MongoDB unavailable. Falling back to accounts.json storage."
        )
        return
    await ensure_indexes()
    logging.getLogger(__name__).info("MongoDB ready.")


async def _post_shutdown(app: Application) -> None:  # type: ignore[type-arg]
    """Gracefully cancel all in-flight login jobs on bot shutdown."""
    from bot.worker import shutdown_all
    cancelled = await shutdown_all(timeout=15.0)
    if cancelled:
        logging.getLogger(__name__).info("Shut down %d active job(s).", cancelled)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in api.env")

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(handle_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot is running. Send /start in Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()
