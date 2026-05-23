"""Verify job creation handlers — Gmail → password → method → TOTP → job dispatch."""

from __future__ import annotations

import logging
import re
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot.accounts import (
    charge_account,
    create_job,
    refund_job,
    save_account,
    update_job_status,
)
from bot.config import VERIFY_PRICE
from bot.ui import (
    cancel_keyboard,
    job_detail_keyboard,
    main_keyboard,
    method_keyboard,
)
from bot.utils import user_identity

from .common import (
    VERIFY_METHODS,
    callback_chat_id,
    clear_input_state,
    edit_message,
    valid_gmail,
)

logger = logging.getLogger(__name__)

_BASE32_SECRET_RE = re.compile(r"^[A-Z2-7]{32}$")


def _normalize_totp_secret(secret: str) -> str:
    normalized = re.sub(r"\s+", "", secret or "").replace("=", "").upper()
    if normalized and not _BASE32_SECRET_RE.fullmatch(normalized):
        raise ValueError("TOTP secret must be a 32-character base32 token")
    return normalized


def _parse_credential_token(text_value: str) -> tuple[str, str, str] | None:
    if "---" not in text_value:
        return None
    parts = text_value.strip().split("---")
    if len(parts) != 3:
        raise ValueError("Use email---password---2fa_secret")
    gmail = parts[0].strip().lower()
    password = parts[1]
    secret = _normalize_totp_secret(parts[2])
    return gmail, password, secret


# ── Job creation ─────────────────────────────────────────────────


async def start_verify_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    account: dict,
    telegram_id: str,
    gmail: str,
    password: str,
    worker_method: str,
    persisted_method: str,
    *,
    query=None,
) -> None:
    """Create a verify job, charge the user, and dispatch to the worker."""
    chat_id = callback_chat_id(update, query)
    if chat_id is None:
        clear_input_state(context)
        if query:
            await edit_message(
                query,
                "<b>Start Verify</b>\n\nCould not resolve the chat. Please try again.",
                main_keyboard(),
            )
        elif update.effective_message:
            await update.effective_message.reply_html(
                "<b>Start Verify</b>\n\nCould not resolve the chat. Please try again.",
                reply_markup=main_keyboard(),
            )
        return

    charged, credit_source, charged_deposit, charged_referral = charge_account(account, VERIFY_PRICE)
    if not charged:
        clear_input_state(context)
        text = "<b>Start Verify</b>\n\n" f"Insufficient balance. You need {VERIFY_PRICE} credit."
        if query:
            await edit_message(query, text, main_keyboard())
        elif update.effective_message:
            await update.effective_message.reply_html(text, reply_markup=main_keyboard())
        return

    job = create_job(
        account,
        gmail,
        password,
        persisted_method,
        VERIFY_PRICE,
        credit_source,
        charged_deposit,
        charged_referral,
    )
    await save_account(telegram_id, account)
    clear_input_state(context)

    created_text = (
        "<b>Verify Job Created</b>\n\n"
        f"<b>Job ID:</b> <code>{escape(str(job['id']))}</code>\n"
        f"<b>Gmail:</b> <code>{escape(gmail)}</code>\n"
        f"<b>Method:</b> {escape(persisted_method)}\n"
        f"<b>Charged:</b> {VERIFY_PRICE} credit\n"
        f"<b>Queue:</b> {escape(credit_source)}\n\n"
        "Live tracking is now running."
    )
    if query:
        await edit_message(query, created_text, job_detail_keyboard(str(job["id"])))
        status_message_id = query.message.message_id if query.message else None
    else:
        if update.effective_message:
            sent = await update.effective_message.reply_html(
                created_text,
                reply_markup=job_detail_keyboard(str(job["id"])),
            )
            status_message_id = sent.message_id
        else:
            # Fallback: effective_message is None (rare edge case)
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=created_text,
                parse_mode="HTML",
                reply_markup=job_detail_keyboard(str(job["id"])),
            )
            status_message_id = sent.message_id

    from bot.worker import start_login_job

    try:
        start_login_job(
            gmail=gmail,
            password=password,
            method=worker_method,
            job_id=str(job["id"]),
            telegram_id=telegram_id,
            bot=context.bot,
            chat_id=chat_id,
            message_id=status_message_id,
            charged=VERIFY_PRICE,
            credit_source=credit_source,
        )
    except RuntimeError as exc:
        logger.warning("Job %s blocked: %s", job.get("id"), exc)
        await update_job_status(
            telegram_id,
            str(job["id"]),
            "FAILED",
            {"progress": 100, "progress_note": str(exc)[:200], "error": "blocked"},
        )
        await refund_job(telegram_id, str(job["id"]))
        fail_text = "<b>Verify Job Failed</b>\n\n" f"{escape(str(exc))}\n\nYour credit has been refunded."
        if query:
            await edit_message(query, fail_text, main_keyboard())
        elif update.effective_message:
            await update.effective_message.reply_html(fail_text, reply_markup=main_keyboard())
    except Exception as exc:
        logger.exception("Failed to schedule login job %s", job.get("id"))
        await update_job_status(
            telegram_id,
            str(job["id"]),
            "FAILED",
            {"progress": 100, "progress_note": "Could not start worker", "error": str(exc)[:200]},
        )
        await refund_job(telegram_id, str(job["id"]))
        fail_text = (
            "<b>Verify Job Failed</b>\n\n"
            "Could not start the worker. Your credit has been refunded."
        )
        if query:
            await edit_message(query, fail_text, main_keyboard())
        elif update.effective_message:
            await update.effective_message.reply_html(fail_text, reply_markup=main_keyboard())


# ── Callback handlers (verify method selection) ──────────────────


async def handle_verify_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query,
    account: dict,
    telegram_id: str,
) -> bool:
    """Handle verify-method callback buttons. Returns True if handled."""
    data = query.data
    if data not in VERIFY_METHODS:
        return False

    gmail = context.user_data.get("verify_gmail")
    password = context.user_data.get("verify_password", "")
    method_key = data
    method = VERIFY_METHODS[method_key]
    worker_method = method
    persisted_method = method

    if not gmail:
        clear_input_state(context)
        context.user_data["awaiting_verify_gmail"] = True
        await edit_message(
            query,
            "<b>✨ Create verify</b>\n\nEnter the Gmail to verify.",
            cancel_keyboard(),
        )
        return True

    if not password:
        context.user_data["awaiting_verify_password"] = True
        await edit_message(
            query,
            "<b>✨ Create verify</b>\n\nEnter the Gmail password.",
            cancel_keyboard(),
        )
        return True

    if method_key == "verify_method_2fa":
        totp_secret = str(context.user_data.get("verify_totp_secret", "")).strip()
        if not totp_secret:
            context.user_data["awaiting_totp_secret"] = True
            context.user_data["verify_method"] = method_key
            await edit_message(
                query,
                "<b>Choose the sign-in verification method.</b>\n\n"
                "Send your <b>2FA / TOTP secret</b> in base32 format.\n\n"
                "Example:\n"
                "<code>JBSWY3DPEHPK3PXP</code>",
                cancel_keyboard(),
            )
            return True
        worker_method = f"2FA Secret:{totp_secret}"
        persisted_method = "2FA Secret"

    await start_verify_job(
        update,
        context,
        account,
        telegram_id,
        str(gmail),
        str(password),
        worker_method,
        persisted_method,
        query=query,
    )
    return True


# ── Text input handlers (gmail, password, TOTP) ─────────────────


async def handle_verify_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    telegram_id: str,
    account: dict,
    text_value: str,
) -> bool:
    """Handle verify-related text input (gmail, password, TOTP). Returns True if handled."""
    if context.user_data is None:
        return False

    # ── Gmail input ──────────────────────────────────────────────
    if context.user_data.get("awaiting_verify_gmail"):
        try:
            credential_token = _parse_credential_token(text_value)
        except ValueError as exc:
            await update.effective_message.reply_html(
                "<b>✨ Create verify</b>\n\n"
                f"{escape(str(exc))}\n\n"
                "Expected format:\n"
                "<code>email---password---2fa_secret</code>",
                reply_markup=cancel_keyboard(),
            )
            return True

        if credential_token is not None:
            gmail, password, secret = credential_token
            if not valid_gmail(gmail):
                await update.effective_message.reply_html(
                    "<b>✨ Create verify</b>\n\n"
                    "Only <code>@gmail.com</code> addresses are supported.",
                    reply_markup=cancel_keyboard(),
                )
                return True
            context.user_data["verify_gmail"] = gmail
            context.user_data["verify_password"] = password
            context.user_data["verify_totp_secret"] = secret
            context.user_data.pop("awaiting_verify_gmail", None)
            await start_verify_job(
                update,
                context,
                account,
                telegram_id,
                gmail,
                password,
                f"2FA Secret:{secret}",
                "2FA Secret",
            )
            return True

        gmail = text_value.strip().lower()
        if not valid_gmail(gmail):
            await update.effective_message.reply_html(
                "<b>✨ Create verify</b>\n\n"
                "Only <code>@gmail.com</code> addresses are supported.",
                reply_markup=cancel_keyboard(),
            )
            return True
        context.user_data["verify_gmail"] = gmail
        context.user_data.pop("awaiting_verify_gmail", None)
        context.user_data["awaiting_verify_password"] = True
        await update.effective_message.reply_html(
            "<b>✨ Create verify</b>\n\n"
            f"✅ Gmail: <code>{escape(gmail)}</code>\n\n"
            "Now enter the Gmail password.",
            reply_markup=cancel_keyboard(),
        )
        return True

    # ── Password input ───────────────────────────────────────────
    if context.user_data.get("awaiting_verify_password"):
        try:
            credential_token = _parse_credential_token(text_value)
        except ValueError as exc:
            await update.effective_message.reply_html(
                "<b>✨ Create verify</b>\n\n"
                f"{escape(str(exc))}",
                reply_markup=cancel_keyboard(),
            )
            return True

        if credential_token is not None:
            gmail, password, secret = credential_token
            if not valid_gmail(gmail):
                await update.effective_message.reply_html(
                    "<b>✨ Create verify</b>\n\n"
                    "Only <code>@gmail.com</code> addresses are supported.",
                    reply_markup=cancel_keyboard(),
                )
                return True
            context.user_data["verify_gmail"] = gmail
            context.user_data["verify_password"] = password
            context.user_data["verify_totp_secret"] = secret
            context.user_data.pop("awaiting_verify_password", None)
            await start_verify_job(
                update,
                context,
                account,
                telegram_id,
                gmail,
                password,
                f"2FA Secret:{secret}",
                "2FA Secret",
            )
            return True

        password = text_value.strip()
        if not password:
            await update.effective_message.reply_html(
                "<b>✨ Create verify</b>\n\nPassword cannot be empty.",
                reply_markup=cancel_keyboard(),
            )
            return True
        context.user_data["verify_password"] = password
        context.user_data.pop("awaiting_verify_password", None)
        await update.effective_message.reply_html(
            "<b>✨ Create verify</b>\n\n"
            f"✅ Gmail: <code>{escape(str(context.user_data.get('verify_gmail', '')))}</code>\n"
            f"✅ Password: <code>{escape('*' * len(password))}</code>\n\n"
            "<b>Choose the sign-in verification method.</b>\n\n"
            "If you choose Verify sign-in, the account must already be signed in on at least one device, "
            "and that device must have internet access to receive the Tap Yes/select-number prompt.",
            reply_markup=method_keyboard(),
        )
        return True

    # ── TOTP secret input ────────────────────────────────────────
    if context.user_data.get("awaiting_totp_secret"):
        try:
            secret = _normalize_totp_secret(text_value)
        except ValueError:
            secret = ""
        if not secret:
            await update.effective_message.reply_html(
                "<b>Choose the sign-in verification method.</b>\n\n"
                "Invalid TOTP secret. It must be a 32-character base32 string.\n\n"
                "Send the correct secret or press Cancel.",
                reply_markup=cancel_keyboard(),
            )
            return True
        context.user_data["verify_totp_secret"] = secret
        context.user_data.pop("awaiting_totp_secret", None)
        gmail = str(context.user_data.get("verify_gmail", ""))
        password = str(context.user_data.get("verify_password", ""))
        if not gmail or not password:
            clear_input_state(context)
            await update.effective_message.reply_html(
                "<b>Start Verify</b>\n\nMissing Gmail or password. Please start again.",
                reply_markup=main_keyboard(),
            )
            return True
        await start_verify_job(
            update,
            context,
            account,
            telegram_id,
            gmail,
            password,
            f"2FA Secret:{secret}",
            "2FA Secret",
        )
        return True

    return False
