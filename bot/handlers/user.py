"""User-facing handlers — /start, main menu routing, text input."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.accounts import (
    add_deposit,
    balance_credit,
    get_account,
    recent_jobs,
    register_referral,
    save_account,
)
from bot.ui import (
    balance_message,
    cancel_keyboard,
    job_detail_keyboard,
    job_detail_message,
    main_keyboard,
    parse_positive_credit,
    profile_message,
    recent_jobs_keyboard,
    recent_jobs_message,
    ref_keyboard,
    referral_invite_link,
    referral_message,
    simple_page,
    start_message,
    topup_message,
)
from bot.utils import user_identity

from .admin import handle_admin_menu, handle_admin_text
from .common import (
    STATIC_PAGES,
    VERIFY_METHODS,
    account_disabled_message,
    clear_input_state,
    edit_message,
    is_admin_id,
    is_banned,
)
from .verify import handle_verify_callback, handle_verify_text

logger = logging.getLogger(__name__)


# ── /start command ───────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command (with optional referral deep-link)."""
    if not update.effective_message:
        return

    telegram_id, _ = user_identity(update)
    referrer_id = None
    if context.args and context.args[0].startswith("ref_"):
        referrer_id = context.args[0].removeprefix("ref_").strip()

    await register_referral(telegram_id, referrer_id)
    await get_account(telegram_id)

    clear_input_state(context)
    await update.effective_message.reply_html(
        start_message(update),
        reply_markup=main_keyboard(),
    )


# ── Main callback router ────────────────────────────────────────


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline-keyboard callback queries."""
    query = update.callback_query
    if not query or not query.data:
        return
    if context.user_data is None:
        return

    await query.answer()
    telegram_id, _ = user_identity(update)
    account = await get_account(telegram_id)

    # ── Admin callbacks ──────────────────────────────────────────
    if query.data.startswith("admin_"):
        await handle_admin_menu(update, context)
        return

    # ── Banned users ─────────────────────────────────────────────
    if is_banned(account) and not is_admin_id(telegram_id):
        clear_input_state(context)
        await edit_message(query, account_disabled_message(), main_keyboard())
        return

    # ── Verify method selection ──────────────────────────────────
    if query.data in VERIFY_METHODS:
        await handle_verify_callback(update, context, query, account, telegram_id)
        return

    # ── Job detail ───────────────────────────────────────────────
    if query.data.startswith("job_"):
        clear_input_state(context)
        job_id = query.data.removeprefix("job_")
        account = await get_account(telegram_id)
        job = next((item for item in recent_jobs(account, 50) if item.get("id") == job_id), None)
        if not job:
            await edit_message(query, "<b>Job Details</b>\n\nJob not found.", job_detail_keyboard(job_id))
            return
        await edit_message(query, job_detail_message(job), job_detail_keyboard(job_id))
        return

    # ── Clear state for all remaining user actions ───────────────
    clear_input_state(context)

    if query.data == "profile":
        await edit_message(query, profile_message(update, account), main_keyboard())
        return

    if query.data == "balance":
        await edit_message(query, balance_message(account), main_keyboard())
        return

    if query.data == "topup":
        if not is_admin_id(telegram_id):
            await edit_message(
                query,
                "<b>Top Up Balance</b>\n\n"
                "Self-service top-up is not available.\n"
                "Contact an admin to add credit to your account.",
                main_keyboard(),
            )
            return
        context.user_data["awaiting_topup"] = True
        await edit_message(query, topup_message(), cancel_keyboard())
        return

    if query.data == "create_verify":
        clear_input_state(context)
        context.user_data["awaiting_verify_gmail"] = True
        await edit_message(
            query,
            "<b>✨ Create verify</b>\n\nEnter the Gmail to verify.",
            cancel_keyboard(),
        )
        return

    if query.data == "recent_jobs":
        await edit_message(query, recent_jobs_message(account), recent_jobs_keyboard(account))
        return

    if query.data == "ref":
        await edit_message(
            query,
            referral_message(update, account),
            ref_keyboard(referral_invite_link(update)),
        )
        return

    if query.data == "back_to_menu":
        await edit_message(query, start_message(update), main_keyboard())
        return

    # ── Static pages (pricing, guide, language) ──────────────────
    title, body = STATIC_PAGES.get(query.data, ("Menu", "Unknown action."))
    await edit_message(query, simple_page(title, body), main_keyboard())


# ── Main text input router ───────────────────────────────────────


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all plain-text messages to the appropriate handler."""
    if not update.effective_message or not update.effective_message.text:
        return
    if context.user_data is None:
        return

    telegram_id, _ = user_identity(update)
    account = await get_account(telegram_id)
    text_value = update.effective_message.text

    # ── Admin text input (lookup, credit, broadcast) ─────────────
    if is_admin_id(telegram_id):
        handled = await handle_admin_text(update, context, telegram_id, text_value)
        if handled:
            return

    # ── Banned users ─────────────────────────────────────────────
    if is_banned(account) and not is_admin_id(telegram_id):
        clear_input_state(context)
        await update.effective_message.reply_html(
            account_disabled_message(),
            reply_markup=main_keyboard(),
        )
        return

    # ── Verify text input (gmail, password, TOTP) ────────────────
    handled = await handle_verify_text(update, context, telegram_id, account, text_value)
    if handled:
        return

    # ── Topup amount ─────────────────────────────────────────────
    if not context.user_data.get("awaiting_topup"):
        return

    # Only admins can top up — prevent free credit minting
    if not is_admin_id(telegram_id):
        clear_input_state(context)
        await update.effective_message.reply_html(
            "<b>Top Up Balance</b>\n\n"
            "Self-service top-up is not available.\n"
            "Contact an admin to add credit to your account.",
            reply_markup=main_keyboard(),
        )
        return

    amount = parse_positive_credit(text_value)
    if amount is None:
        await update.effective_message.reply_html(
            "<b>Top Up Balance</b>\n\nPlease enter a positive whole amount.",
            reply_markup=cancel_keyboard(),
        )
        return

    add_deposit(account, amount)
    await save_account(telegram_id, account)
    clear_input_state(context)

    await update.effective_message.reply_html(
        "<b>Top Up Balance</b>\n\n"
        f"Added: <b>{amount} credit</b>\n"
        f"Current balance: <b>{balance_credit(account)} credit</b>",
        reply_markup=main_keyboard(),
    )
