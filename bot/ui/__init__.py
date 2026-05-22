"""Telegram UI helpers: keyboards, messages, formatting.

Split into sub-modules for maintainability:
- formatting.py  — progress bars, status icons, text helpers
- keyboards.py   — InlineKeyboardMarkup builders
- messages.py    — HTML message formatters
"""

from bot.ui.formatting import (
    ADMIN_USERS_PAGE_SIZE,
    PROGRESS_WIDTH,
    RECENT_JOB_LIMIT,
    compact_note,
    parse_positive_credit,
    progress_bar,
    progress_flow,
    progress_line,
    progress_stage,
    safe_int,
    short_text,
    stage_emoji,
    status_badge,
    status_emoji,
    status_label,
)
from bot.ui.keyboards import (
    admin_confirm_refund_keyboard,
    admin_credit_confirm_keyboard,
    admin_jobs_keyboard,
    admin_keyboard,
    admin_user_keyboard,
    admin_users_keyboard,
    cancel_keyboard,
    job_detail_keyboard,
    main_keyboard,
    method_keyboard,
    recent_jobs_keyboard,
    ref_keyboard,
)
from bot.ui.messages import (
    admin_broadcast_prompt,
    admin_credit_confirm_message,
    admin_credit_history_message,
    admin_credit_prompt_message,
    admin_dashboard_message,
    admin_lookup_prompt,
    admin_recent_jobs_message,
    admin_user_message,
    admin_users_message,
    balance_message,
    job_detail_message,
    profile_message,
    recent_jobs_message,
    referral_invite_link,
    referral_message,
    simple_page,
    start_message,
    topup_message,
)

__all__ = [
    # formatting
    "ADMIN_USERS_PAGE_SIZE", "PROGRESS_WIDTH", "RECENT_JOB_LIMIT",
    "compact_note", "parse_positive_credit", "progress_bar", "progress_flow",
    "progress_line", "progress_stage", "safe_int", "short_text", "stage_emoji",
    "status_badge", "status_emoji", "status_label",
    # keyboards
    "admin_confirm_refund_keyboard", "admin_credit_confirm_keyboard",
    "admin_jobs_keyboard", "admin_keyboard", "admin_user_keyboard",
    "admin_users_keyboard", "cancel_keyboard", "job_detail_keyboard",
    "main_keyboard", "method_keyboard", "recent_jobs_keyboard", "ref_keyboard",
    # messages
    "admin_broadcast_prompt", "admin_credit_confirm_message",
    "admin_credit_history_message", "admin_credit_prompt_message",
    "admin_dashboard_message", "admin_lookup_prompt", "admin_recent_jobs_message",
    "admin_user_message", "admin_users_message", "balance_message",
    "job_detail_message", "profile_message", "recent_jobs_message",
    "referral_invite_link", "referral_message", "simple_page", "start_message",
    "topup_message",
]
