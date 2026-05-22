"""Shared handler utilities: message editing, state management, validators."""

from __future__ import annotations

import logging
import re
from html import escape

from telegram import Update
from telegram.error import BadRequest, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from bot.config import ADMIN_USER_IDS, VERIFY_PRICE

logger = logging.getLogger(__name__)

GMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@gmail\.com$", re.IGNORECASE)

INPUT_STATE_KEYS = (
    "awaiting_topup",
    "awaiting_verify",
    "awaiting_verify_gmail",
    "awaiting_verify_password",
    "awaiting_totp_secret",
    "verify_gmail",
    "verify_password",
    "verify_method",
    "verify_totp_secret",
    "admin_selected_user",
    "admin_credit_action",
    "admin_credit_amount",
    "admin_credit_before",
    "admin_credit_after",
    "admin_credit_pending",
    "awaiting_admin_lookup",
    "awaiting_admin_credit",
    "awaiting_admin_broadcast",
)

VERIFY_METHODS = {
    "verify_method_2fa": "2FA Secret",
    "verify_method_signin": "Verify sign-in",
}

STATIC_PAGES = {
    "pricing": ("Pricing", f"Verify price: {VERIFY_PRICE} credit per job."),
    "guide": (
        "Guide",
        "Pre-check before starting:\n"
        "- Gmail account is accessible\n"
        "- Correct password is ready\n"
        "- Recovery or verification device is available\n"
        "- For Verify sign-in, the signed-in device is online\n"
        "- For 2FA Secret, the base32 secret is valid",
    ),
    "language": ("Language", "Current language: English."),
}


# ── Message helpers ──────────────────────────────────────────────


async def edit_message(query, text: str, reply_markup) -> None:
    """Edit a callback query message with safe error handling."""
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except RetryAfter as exc:
        logger.info("Telegram rate limited edit for %.1fs", float(exc.retry_after))
        return
    except TimedOut:
        logger.warning("Telegram timed out while editing callback message")
        return
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return
        raise


# ── State management ─────────────────────────────────────────────


def clear_input_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove all transient input-state keys from user_data."""
    if not context or context.user_data is None:
        return
    for key in INPUT_STATE_KEYS:
        context.user_data.pop(key, None)


async def delete_user_input_message(update: Update) -> None:
    """Best-effort delete of the user's text message (privacy cleanup)."""
    message = update.effective_message
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        logger.debug("Could not delete user input message", exc_info=True)


# ── Validators ───────────────────────────────────────────────────


def valid_gmail(value: str) -> bool:
    """Check if *value* looks like a @gmail.com address."""
    return bool(GMAIL_RE.fullmatch(value.strip()))


def callback_chat_id(update: Update, query) -> int | None:
    """Resolve the chat ID from an Update or its callback query."""
    if update.effective_chat:
        return update.effective_chat.id
    if query and query.message:
        return query.message.chat.id
    return None


def safe_referral_count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def is_admin_id(telegram_id: str) -> bool:
    return telegram_id in ADMIN_USER_IDS


def parse_admin_credit_amount(value: str, allow_zero: bool = False) -> int | None:
    """Parse a user-supplied credit amount string. Returns None on invalid input."""
    cleaned = value.strip().replace(",", "")
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].strip()
    if not cleaned.isdigit():
        return None
    try:
        amount = int(cleaned)
    except ValueError:
        return None
    if amount > 0 or (allow_zero and amount == 0):
        return amount
    return None


def is_banned(account: dict) -> bool:
    return str(account.get("status", "active")).lower() == "banned"


def account_disabled_message() -> str:
    return "<b>Account disabled</b>\n\nYour account is disabled. Contact support."


def admin_denied_message(telegram_id: str) -> str:
    configured = ", ".join(sorted(ADMIN_USER_IDS)) or "none"
    return (
        "<b>Access denied</b>\n\n"
        f"Your Telegram ID: <code>{escape(telegram_id)}</code>\n"
        f"Allowed admin IDs: <code>{escape(configured)}</code>"
    )
