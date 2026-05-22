"""Admin panel handlers — /admin, user management, credit operations, broadcast."""

from __future__ import annotations

import logging
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from bot.accounts import (
    adjust_deposit_credit,
    admin_stats,
    all_recent_jobs,
    get_account,
    list_account_ids,
    preview_deposit_credit_change,
    recent_jobs,
    refund_job,
    set_account_status,
)
from bot.ui import (
    ADMIN_USERS_PAGE_SIZE,
    admin_broadcast_prompt,
    admin_confirm_refund_keyboard,
    admin_credit_confirm_keyboard,
    admin_credit_confirm_message,
    admin_credit_history_message,
    admin_credit_prompt_message,
    admin_dashboard_message,
    admin_jobs_keyboard,
    admin_keyboard,
    admin_lookup_prompt,
    admin_recent_jobs_message,
    admin_user_keyboard,
    admin_user_message,
    admin_users_keyboard,
    admin_users_message,
    main_keyboard,
)
from bot.utils import user_identity

from .common import (
    admin_denied_message,
    clear_input_state,
    edit_message,
    is_admin_id,
    parse_admin_credit_amount,
)

logger = logging.getLogger(__name__)


# ── /admin command ───────────────────────────────────────────────


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /admin command."""
    if not update.effective_message:
        return
    if context.user_data is None:
        return

    telegram_id, _ = user_identity(update)
    if not is_admin_id(telegram_id):
        await update.effective_message.reply_html(admin_denied_message(telegram_id))
        return

    await get_account(telegram_id)
    clear_input_state(context)
    await update.effective_message.reply_html(
        admin_dashboard_message(await admin_stats()),
        reply_markup=admin_keyboard(),
    )


# ── Admin user list helpers ──────────────────────────────────────


async def show_admin_users(query, page: int = 0) -> None:
    ids = await list_account_ids()
    total_users = len(ids)
    total_pages = max(1, (total_users + ADMIN_USERS_PAGE_SIZE - 1) // ADMIN_USERS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * ADMIN_USERS_PAGE_SIZE
    page_ids = ids[start : start + ADMIN_USERS_PAGE_SIZE]
    users = [(user_id, await get_account(user_id)) for user_id in page_ids]
    await edit_message(
        query,
        admin_users_message(users, page, total_pages, total_users),
        admin_users_keyboard(users, page, total_pages),
    )


async def show_admin_user(query, telegram_id: str) -> None:
    account = await get_account(telegram_id)
    await edit_message(
        query,
        admin_user_message(telegram_id, account),
        admin_user_keyboard(telegram_id, str(account.get("status", "active"))),
    )


# ── Admin callback menu ─────────────────────────────────────────


async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all admin_* callback queries."""
    query = update.callback_query
    if not query or not query.data:
        return
    if context.user_data is None:
        return

    telegram_id, _ = user_identity(update)
    if not is_admin_id(telegram_id):
        await edit_message(query, admin_denied_message(telegram_id), main_keyboard())
        return

    data = query.data

    if data in {"admin_home", "admin_stats"}:
        clear_input_state(context)
        await edit_message(query, admin_dashboard_message(await admin_stats()), admin_keyboard())
        return

    if data == "admin_users":
        clear_input_state(context)
        await show_admin_users(query, 0)
        return

    if data.startswith("admin_users_page_"):
        clear_input_state(context)
        page_value = data.removeprefix("admin_users_page_")
        page = int(page_value) if page_value.isdigit() else 0
        await show_admin_users(query, page)
        return

    if data == "admin_lookup":
        clear_input_state(context)
        context.user_data["awaiting_admin_lookup"] = True
        await edit_message(query, admin_lookup_prompt(), admin_keyboard())
        return

    if data == "admin_broadcast":
        clear_input_state(context)
        context.user_data["awaiting_admin_broadcast"] = True
        await edit_message(query, admin_broadcast_prompt(), admin_keyboard())
        return

    if data == "admin_recent_jobs":
        clear_input_state(context)
        jobs = await all_recent_jobs(10)
        await edit_message(query, admin_recent_jobs_message(jobs), admin_keyboard())
        return

    if data.startswith("admin_user_jobs_"):
        target_id = data.removeprefix("admin_user_jobs_")
        account = await get_account(target_id)
        jobs = recent_jobs(account, 10)
        text = f"<b>User jobs</b>\n\nUser: <code>{escape(target_id)}</code>"
        if not jobs:
            text += "\n\nNo jobs found."
        await edit_message(query, text, admin_jobs_keyboard(target_id, jobs))
        return

    if data.startswith("admin_credit_history_"):
        target_id = data.removeprefix("admin_credit_history_")
        account = await get_account(target_id)
        await edit_message(
            query,
            admin_credit_history_message(target_id, account),
            admin_user_keyboard(target_id, str(account.get("status", "active"))),
        )
        return

    if data.startswith("admin_user_"):
        target_id = data.removeprefix("admin_user_")
        clear_input_state(context)
        await show_admin_user(query, target_id)
        return

    if (
        data.startswith("admin_add_credit_")
        or data.startswith("admin_remove_credit_")
        or data.startswith("admin_set_credit_")
    ):
        if data.startswith("admin_add_credit_"):
            action = "add"
            target_id = data.removeprefix("admin_add_credit_")
        elif data.startswith("admin_remove_credit_"):
            action = "remove"
            target_id = data.removeprefix("admin_remove_credit_")
        else:
            action = "set"
            target_id = data.removeprefix("admin_set_credit_")
        target_account = await get_account(target_id)
        context.user_data["admin_selected_user"] = target_id
        context.user_data["admin_credit_action"] = action
        context.user_data["awaiting_admin_credit"] = True
        await edit_message(
            query,
            admin_credit_prompt_message(target_id, target_account, action),
            admin_user_keyboard(target_id, str(target_account.get("status", "active"))),
        )
        return

    if data == "admin_confirm_credit":
        pending = bool(context.user_data.get("admin_credit_pending"))
        target_id = str(context.user_data.get("admin_selected_user", ""))
        action = str(context.user_data.get("admin_credit_action", ""))
        amount = int(context.user_data.get("admin_credit_amount", 0))
        if not pending or not target_id:
            clear_input_state(context)
            await edit_message(query, "<b>Credit</b>\n\nConfirmation expired.", admin_keyboard())
            return
        ok, new_credit, error = await adjust_deposit_credit(target_id, action, amount, telegram_id)
        clear_input_state(context)
        if not ok:
            await edit_message(query, f"<b>Credit</b>\n\n{escape(error)}", admin_keyboard())
            return
        target_account = await get_account(target_id)
        await edit_message(
            query,
            f"<b>Credit updated</b>\n\n"
            f"User: <code>{escape(target_id)}</code>\n"
            f"Deposit credit: <b>{new_credit}</b>",
            admin_user_keyboard(target_id, str(target_account.get("status", "active"))),
        )
        return

    if data.startswith("admin_cancel_credit_"):
        target_id = data.removeprefix("admin_cancel_credit_")
        clear_input_state(context)
        await show_admin_user(query, target_id)
        return

    if data.startswith("admin_ban_") or data.startswith("admin_unban_"):
        banning = data.startswith("admin_ban_")
        target_id = data.removeprefix("admin_ban_" if banning else "admin_unban_")
        await set_account_status(target_id, "banned" if banning else "active")
        await show_admin_user(query, target_id)
        return

    if data.startswith("admin_confirm_refund_"):
        payload = data.removeprefix("admin_confirm_refund_")
        target_id, _, job_id = payload.partition("_")
        ok = await refund_job(target_id, job_id)
        message = "Refunded successfully." if ok else "Job was already refunded or cannot be refunded."
        target_account = await get_account(target_id)
        await edit_message(
            query,
            f"<b>Refund</b>\n\n{escape(message)}",
            admin_jobs_keyboard(target_id, recent_jobs(target_account, 10)),
        )
        return

    if data.startswith("admin_refund_"):
        payload = data.removeprefix("admin_refund_")
        target_id, _, job_id = payload.partition("_")
        target_account = await get_account(target_id)
        job = next((j for j in recent_jobs(target_account, 50) if str(j.get("id")) == job_id), None)
        if not job:
            await edit_message(query, "<b>Refund</b>\n\nJob not found.", admin_keyboard())
            return
        text = (
            "<b>Refund job?</b>\n\n"
            f"User: <code>{escape(target_id)}</code>\n"
            f"Job: <code>{escape(job_id)}</code>\n"
            f"Charged: {int(job.get('charged', 0))} credit\n"
            f"Refunded: {escape(str(job.get('refunded', False)))}"
        )
        await edit_message(query, text, admin_confirm_refund_keyboard(target_id, job_id))
        return

    await edit_message(query, "<b>Admin</b>\n\nUnknown action.", admin_keyboard())


# ── Admin text input handlers ────────────────────────────────────


async def handle_admin_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    telegram_id: str,
    text_value: str,
) -> bool:
    """Handle admin-related text input (lookup, credit, broadcast). Returns True if handled."""
    if context.user_data is None:
        return False

    # ── User lookup ──────────────────────────────────────────────
    if context.user_data.get("awaiting_admin_lookup"):
        target_id = text_value.strip()
        clear_input_state(context)
        if not target_id.isdigit():
            await update.effective_message.reply_html(
                "<b>Lookup user</b>\n\nSend a numeric Telegram ID.",
                reply_markup=admin_keyboard(),
            )
            return True
        target_account = await get_account(target_id)
        await update.effective_message.reply_html(
            admin_user_message(target_id, target_account),
            reply_markup=admin_user_keyboard(target_id, str(target_account.get("status", "active"))),
        )
        return True

    # ── Credit adjustment ────────────────────────────────────────
    if context.user_data.get("awaiting_admin_credit"):
        target_id = str(context.user_data.get("admin_selected_user", ""))
        action = str(context.user_data.get("admin_credit_action", "add"))
        amount = parse_admin_credit_amount(text_value, allow_zero=action == "set")
        if amount is None:
            await update.effective_message.reply_html(
                "<b>Credit</b>\n\nPlease enter a valid whole amount.",
                reply_markup=admin_keyboard(),
            )
            return True
        target_account = await get_account(target_id)
        before_balance = int(target_account.get("deposit_credit", 0))
        ok, after_balance, error = preview_deposit_credit_change(before_balance, action, amount)
        if not ok:
            await update.effective_message.reply_html(
                f"<b>Credit</b>\n\n{escape(error)}",
                reply_markup=admin_keyboard(),
            )
            return True
        context.user_data["awaiting_admin_credit"] = False
        context.user_data["admin_credit_amount"] = amount
        context.user_data["admin_credit_before"] = before_balance
        context.user_data["admin_credit_after"] = after_balance
        context.user_data["admin_credit_pending"] = True
        await update.effective_message.reply_html(
            admin_credit_confirm_message(target_id, action, amount, before_balance, after_balance),
            reply_markup=admin_credit_confirm_keyboard(target_id),
        )
        return True

    # ── Broadcast ────────────────────────────────────────────────
    if context.user_data.get("awaiting_admin_broadcast"):
        message = text_value.strip()
        clear_input_state(context)
        if not message:
            await update.effective_message.reply_html(
                "<b>Broadcast</b>\n\nMessage cannot be empty.",
                reply_markup=admin_keyboard(),
            )
            return True
        safe_message = escape(message)
        sent = 0
        failed = 0
        for target_id in await list_account_ids():
            try:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text=safe_message,
                    parse_mode="HTML",
                )
                sent += 1
            except Exception as exc:
                failed += 1
                logger.warning("Broadcast failed for %s: %s", target_id, exc)
        await update.effective_message.reply_html(
            "<b>Broadcast complete</b>\n\n"
            f"Sent: <b>{sent}</b>\n"
            f"Failed: <b>{failed}</b>",
            reply_markup=admin_keyboard(),
        )
        return True

    return False
