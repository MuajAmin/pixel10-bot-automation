"""Inline keyboard builders for all Telegram menus."""

from __future__ import annotations

from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.accounts import balance_credit, recent_jobs
from .formatting import RECENT_JOB_LIMIT, safe_int, short_text, status_emoji


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✨ Create Verify", callback_data="create_verify"),
                InlineKeyboardButton("📋 Recent Jobs", callback_data="recent_jobs"),
            ],
            [
                InlineKeyboardButton("💳 Balance", callback_data="balance"),
                InlineKeyboardButton("💸 Top Up", callback_data="topup"),
            ],
            [
                InlineKeyboardButton("👤 Profile", callback_data="profile"),
                InlineKeyboardButton("🎁 Referral", callback_data="ref"),
            ],
            [
                InlineKeyboardButton("📘 Guide", callback_data="guide"),
                InlineKeyboardButton("💰 Pricing", callback_data="pricing"),
            ],
            [InlineKeyboardButton("🌐 Language", callback_data="language")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✖ Cancel", callback_data="back_to_menu")]]
    )


def ref_keyboard(invite_link: str | None = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🔄 Refresh", callback_data="ref")]]
    if invite_link:
        rows.append(
            [
                InlineKeyboardButton(
                    "📨 Share Invite",
                    url=f"https://t.me/share/url?url={quote(invite_link, safe='')}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def recent_jobs_keyboard(account: dict) -> InlineKeyboardMarkup:
    rows = []
    for job in recent_jobs(account, RECENT_JOB_LIMIT):
        status = str(job.get("status", "PENDING")).upper()
        progress = max(0, min(100, safe_int(job.get("progress"))))
        email = short_text(str(job.get("gmail", "unknown")), 18)
        label = f"{status_emoji(status)} {progress:>3}% {email}"
        job_id = job.get("id")
        if job_id:
            rows.append([InlineKeyboardButton(label, callback_data=f"job_{job_id}")])

    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def job_detail_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"job_{job_id}"),
                InlineKeyboardButton("📋 All Jobs", callback_data="recent_jobs"),
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu")],
        ]
    )


def method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔐 2FA Secret", callback_data="verify_method_2fa")],
            [InlineKeyboardButton("✅ Verify Sign-In", callback_data="verify_method_signin")],
            [InlineKeyboardButton("✖ Cancel", callback_data="back_to_menu")],
        ]
    )


# ── Admin keyboards ─────────────────────────────────────────────


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
                InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            ],
            [
                InlineKeyboardButton("🔎 Lookup user", callback_data="admin_lookup"),
                InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast"),
            ],
            [InlineKeyboardButton("📋 Recent jobs", callback_data="admin_recent_jobs")],
            [InlineKeyboardButton("🏠 User menu", callback_data="back_to_menu")],
        ]
    )


def admin_users_keyboard(users: list[tuple[str, dict]], page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{'🚫' if str(account.get('status', 'active')).lower() == 'banned' else '👤'} "
                f"{user_id} • {balance_credit(account)} cr",
                callback_data=f"admin_user_{user_id}",
            )
        ]
        for user_id, account in users
    ]
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"admin_users_page_{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"admin_users_page_{page}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton("Next ➡", callback_data=f"admin_users_page_{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔎 Lookup user", callback_data="admin_lookup")])
    rows.append([InlineKeyboardButton("⬅ Admin", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


def admin_user_keyboard(telegram_id: str, status: str = "active") -> InlineKeyboardMarkup:
    status = status.lower()
    status_button = (
        InlineKeyboardButton("✅ Unban", callback_data=f"admin_unban_{telegram_id}")
        if status == "banned"
        else InlineKeyboardButton("🚫 Ban", callback_data=f"admin_ban_{telegram_id}")
    )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add credit", callback_data=f"admin_add_credit_{telegram_id}"),
                InlineKeyboardButton("➖ Remove credit", callback_data=f"admin_remove_credit_{telegram_id}"),
            ],
            [
                InlineKeyboardButton("🎯 Set balance", callback_data=f"admin_set_credit_{telegram_id}"),
                InlineKeyboardButton("🧾 Credit history", callback_data=f"admin_credit_history_{telegram_id}"),
            ],
            [
                status_button,
                InlineKeyboardButton("📋 Jobs", callback_data=f"admin_user_jobs_{telegram_id}"),
            ],
            [InlineKeyboardButton("⬅ Admin", callback_data="admin_home")],
        ]
    )


def admin_jobs_keyboard(telegram_id: str, jobs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs[:10]:
        job_id = str(job.get("id", ""))
        if not job_id:
            continue
        refunded = " ↩" if job.get("refunded") else ""
        label = (
            f"{status_emoji(str(job.get('status', 'PENDING')))} "
            f"{short_text(str(job.get('gmail', 'unknown')), 18)}{refunded}"
        )
        rows.append([InlineKeyboardButton(label, callback_data=f"admin_refund_{telegram_id}_{job_id}")])
    rows.append([InlineKeyboardButton("⬅ User", callback_data=f"admin_user_{telegram_id}")])
    return InlineKeyboardMarkup(rows)


def admin_confirm_refund_keyboard(telegram_id: str, job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩ Refund job", callback_data=f"admin_confirm_refund_{telegram_id}_{job_id}")],
            [InlineKeyboardButton("⬅ Jobs", callback_data=f"admin_user_jobs_{telegram_id}")],
        ]
    )


def admin_credit_confirm_keyboard(telegram_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data="admin_confirm_credit"),
                InlineKeyboardButton("✖ Cancel", callback_data=f"admin_cancel_credit_{telegram_id}"),
            ],
            [InlineKeyboardButton("⬅ User", callback_data=f"admin_user_{telegram_id}")],
        ]
    )
