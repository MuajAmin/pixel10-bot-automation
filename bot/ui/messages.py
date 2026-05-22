"""HTML message formatters for Telegram bot responses."""

from __future__ import annotations

from html import escape

from bot.accounts import (
    CREDIT_ACTION_LABELS,
    balance_credit,
    recent_jobs,
    referral_credit,
    referral_earned_credit,
    remaining_for_reward,
    total_spent,
)
from bot.config import BOT_TITLE, BOT_USERNAME, DEFAULT_NAME, VERIFY_PRICE
from bot.utils import user_identity

from .formatting import (
    RECENT_JOB_LIMIT,
    compact_note,
    progress_bar,
    progress_flow,
    progress_stage,
    safe_int,
    short_text,
    stage_emoji,
    status_badge,
    status_emoji,
)


def start_message(update) -> str:
    user = update.effective_user
    user_name = (
        user.first_name
        if user and user.first_name
        else (user.username if user and user.username else DEFAULT_NAME or "User")
    )

    return (
        f"✨ <b>{escape(BOT_TITLE)}</b> ✨\n"
        f"{'━' * 26}\n\n"
        f"👋 Hello, <b>{escape(user_name)}</b>!\n\n"
        "<b>What I can do:</b>\n"
        "• 🔐 Auto-login to Gmail accounts\n"
        "• 🎁 Claim Pixel/Gemini offers\n"
        "• 📊 Track every step in real-time\n\n"
        f"{'─' * 26}\n"
        f"💰 <b>Price:</b> {VERIFY_PRICE} credit per job\n"
        f"{'─' * 26}\n\n"
        "📖 Open <b>Guide</b> before your first job."
    )


def profile_message(update, account: dict) -> str:
    telegram_id, username = user_identity(update)
    status_text = str(account.get('status', 'active'))
    status_icon = "🟢" if status_text == "active" else "🔴"

    return (
        "👤 <b>Account Profile</b>\n"
        f"{'━' * 26}\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{escape(telegram_id)}</code>\n"
        f"👤 <b>Username:</b> @{escape(username.lstrip('@'))}\n"
        f"{status_icon} <b>Status:</b> {escape(status_text)}\n\n"
        f"{'─' * 26}\n"
        "💳 <b>Balance Overview</b>\n\n"
        f"   💰 Available: <b>{balance_credit(account)} credit</b>\n"
        f"   📥 Deposit:   {safe_int(account.get('deposit_credit'))} credit\n"
        f"   🎁 Referral:  {referral_credit(account)} credit\n\n"
        f"{'─' * 26}\n"
        "📊 <b>Activity</b>\n\n"
        f"   📥 Total deposited: {safe_int(account.get('total_deposit'))} credit\n"
        f"   📤 Total spent:     {total_spent(account)} credit"
    )


def balance_message(account: dict) -> str:
    total = balance_credit(account)
    deposit = safe_int(account.get('deposit_credit'))
    referral = referral_credit(account)

    return (
        "💳 <b>Balance</b>\n"
        f"{'━' * 26}\n\n"
        f"💰 <b>Available:</b> <b>{total} credit</b>\n\n"
        f"   📥 Deposit:  {deposit} credit\n"
        f"   🎁 Referral: {referral} credit\n\n"
        f"{'─' * 26}\n"
        f"💲 Verify job price: <b>{VERIFY_PRICE} credit</b>"
    )


def topup_message() -> str:
    return (
        "<b>Top Up Balance</b>\n\n"
        "Enter the amount you want to add as credit.\n"
        "Example: <code>10</code>"
    )


def recent_jobs_message(account: dict) -> str:
    jobs = recent_jobs(account, RECENT_JOB_LIMIT)
    if not jobs:
        return (
            "📋 <b>Recent Jobs</b>\n"
            f"{'━' * 26}\n\n"
            "No jobs found yet.\n\n"
            "Tap <b>Create Verify</b> to start your first job."
        )

    shown = jobs[:RECENT_JOB_LIMIT]
    # Sort: running jobs first, then by progress descending
    pinned = sorted(
        shown,
        key=lambda job: (
            str(job.get("status", "PENDING")).upper() not in {"RUNNING", "PROCESSING", "PENDING"},
            -safe_int(job.get("progress")),
        ),
    )
    running = sum(
        1 for job in shown if str(job.get("status", "PENDING")).upper() in {"RUNNING", "PROCESSING", "PENDING"}
    )
    success = sum(
        1 for job in shown if str(job.get("status", "PENDING")).upper() in {"SUCCESS", "SUCCEEDED", "COMPLETED"}
    )
    failed = sum(1 for job in shown if str(job.get("status", "PENDING")).upper() in {"FAILED", "ERROR"})

    lines = [
        "📋 <b>Recent Jobs</b>",
        f"{'━' * 26}",
        "",
        f"🟢 Live <b>{running}</b>  •  ✅ Done <b>{success}</b>  •  ❌ Failed <b>{failed}</b>",
        "<i>Most active jobs stay at the top.</i>",
        "",
    ]

    for job in pinned:
        status = str(job.get("status", "PENDING")).upper()
        progress = max(0, min(100, safe_int(job.get("progress"))))
        stage = progress_stage(progress, status)
        note = str(job.get("progress_note", "")).strip()
        gmail = str(job.get("gmail", "unknown"))

        lines.append(f"<code>{escape(gmail)}</code>")
        lines.append(f"{status_badge(status)}  •  <b>{progress}%</b>")
        lines.append(f"📍 <b>Stage:</b> {stage_emoji(stage)} {escape(stage)}")
        lines.append(f"<code>{progress_bar(progress)}</code>")
        if note:
            lines.append(f"💬 <b>Update:</b> {escape(compact_note(note))}")
        lines.append("")

    lines.append("Tap a job below to open the full status view.")
    return "\n".join(lines).strip()


def job_detail_message(job: dict) -> str:
    status = str(job.get("status", "PENDING")).upper()
    progress = max(0, min(100, safe_int(job.get("progress"))))
    completed = status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}
    failed = status in {"FAILED", "ERROR"}
    stage = progress_stage(progress, status)
    method = str(job.get("method", "N/A"))

    if failed:
        headline = "❌ <b>Job Failed</b>"
    elif completed:
        headline = "🎉 <b>Job Completed</b>"
    else:
        headline = "🟢 <b>Job In Progress</b>"

    lines = [
        headline,
        f"{'━' * 28}",
        "",
        f"{status_badge(status)}",
        f"📍 <b>Stage:</b> {stage_emoji(stage)} {escape(stage)}",
        f"📊 <b>Progress:</b> <b>{progress}%</b>",
        f"<code>{progress_bar(progress)}</code>",
        f"🧭 <b>Flow:</b> {escape(progress_flow(progress, status))}",
        "",
    ]

    lines.append("📝 <b>Job Details</b>")
    lines.append(f"{'─' * 28}")
    lines.append("")
    lines.append(f"📧 <b>Account:</b> <code>{escape(str(job.get('gmail', '')))}</code>")
    lines.append(f"🆔 <b>ID:</b> <code>{escape(str(job.get('id', '')))}</code>")
    lines.append(f"🔐 <b>Method:</b> {escape(method)}")
    lines.append(f"💳 <b>Charged:</b> {safe_int(job.get('charged'))} credit ({escape(str(job.get('credit_source', 'N/A')))})")
    lines.append(f"📌 <b>Raw code:</b> <code>{escape(status)}</code>")

    note = job.get("progress_note", "")
    if note:
        lines.append("")
        lines.append("💬 <b>Latest Update</b>")
        lines.append(f"{'─' * 28}")
        lines.append(escape(" ".join(str(note).split())))

    offer_result = job.get("offer_result", "")
    if offer_result:
        lines.append("")
        lines.append("🎁 <b>Offer Result</b>")
        lines.append(f"{'─' * 28}")
        lines.append("")
        offer_icon = {
            "CLAIMED": "🎉", "ALREADY_ACTIVE": "ℹ️",
            "NOT_ELIGIBLE": "⚠️", "NOT_FOUND": "🔍",
            "PAYMENT_REQUIRED": "💳", "MANUAL_REQUIRED": "✋",
            "CLAIM_FAILED": "❌", "CLAIMABLE": "✨",
        }.get(str(offer_result).upper(), "❔")
        lines.append(f"{offer_icon} <b>Result:</b> {escape(str(offer_result))}")
        offer_reason = job.get("offer_reason", "")
        if offer_reason:
            lines.append(f"💬 <b>Reason:</b> {escape(str(offer_reason))}")

    redeem = job.get("redeem_link", "")
    if redeem:
        lines.append("")
        lines.append("🔗 <b>Redeem Link</b>")
        lines.append(f"{'─' * 28}")
        lines.append(escape(str(redeem)))

    error = job.get("error", "")
    if error:
        lines.append("")
        lines.append("⚠️ <b>Error Details</b>")
        lines.append(f"{'─' * 28}")
        lines.append(f"<code>{escape(str(error))}</code>")
        lines.append("")
        lines.append("💡 Fix the issue and retry with a new job.")

    refunded = job.get("refunded")
    if refunded:
        lines.append("")
        lines.append(f"↩️ <b>Refunded:</b> {safe_int(refunded)} credit")

    if not completed and not failed:
        lines.append("")
        lines.append(f"{'━' * 28}")
        lines.append("🔄 Tap <b>Refresh</b> for the latest progress.")

    return "\n".join(lines)


def referral_invite_link(update) -> str:
    telegram_id, _ = user_identity(update)
    return f"https://t.me/{BOT_USERNAME}?start=ref_{telegram_id}"


def referral_message(update, account: dict) -> str:
    invite_link = referral_invite_link(update)
    share_text = f"Join {BOT_TITLE} with my referral link: {invite_link}"

    return (
        f"<b>{escape(BOT_TITLE)} Referral</b>\n\n"
        "<b>Your invite link</b>\n"
        f"<code>{escape(invite_link)}</code>\n\n"
        f"Valid invited users: <b>{safe_int(account.get('valid_invited_users'))}</b>\n"
        f"Pending referrals: <b>{safe_int(account.get('pending_referrals'))}</b>\n"
        f"Earned referral credit: <b>{referral_earned_credit(account)}</b>\n"
        f"Available referral credit: <b>{referral_credit(account)}</b>\n"
        f"Remaining for next 1 credit: <b>{remaining_for_reward(account)}</b>\n\n"
        "<b>Share text</b>\n"
        f"<code>{escape(share_text)}</code>"
    )


def simple_page(title: str, body: str) -> str:
    return f"<b>{escape(title)}</b>\n\n{escape(body)}"


# ── Admin messages ───────────────────────────────────────────────


def admin_dashboard_message(stats: dict[str, int]) -> str:
    return (
        "<b>🛠 Admin panel</b>\n\n"
        f"👥 Users: <b>{safe_int(stats.get('total_users'))}</b>\n"
        f"✅ Active: {safe_int(stats.get('active_users'))}\n"
        f"🚫 Banned: {safe_int(stats.get('banned_users'))}\n"
        f"💳 Total balance: {safe_int(stats.get('total_balance'))} credit\n"
        f"📥 Total deposit: {safe_int(stats.get('total_deposit'))} credit\n"
        f"📤 Total spent: {safe_int(stats.get('total_spent'))} credit\n"
        f"📋 Jobs: {safe_int(stats.get('total_jobs'))}\n"
        f"❌ Failed jobs: {safe_int(stats.get('failed_jobs'))}"
    )


def admin_lookup_prompt() -> str:
    return "<b>🔎 Lookup user</b>\n\nSend the Telegram user ID."


def admin_broadcast_prompt() -> str:
    return (
        "<b>📣 Broadcast</b>\n\n"
        "Send the HTML message to deliver to every account in accounts.json."
    )


def admin_users_message(users: list[tuple[str, dict]], page: int, total_pages: int, total_users: int) -> str:
    if not users:
        return "<b>👥 Users</b>\n\nNo accounts found."
    lines = [
        "<b>👥 Users</b>",
        f"Page <b>{page + 1}/{total_pages}</b> • Total <b>{total_users}</b>",
        "",
    ]
    for user_id, account in users:
        status = str(account.get("status", "active")).lower()
        status_icon = "🚫" if status == "banned" else "✅"
        lines.append(
            f"{status_icon} <code>{escape(user_id)}</code> • "
            f"{balance_credit(account)} cr • {escape(status)}"
        )
    lines.append("")
    lines.append("Select a user below to manage credit.")
    return "\n".join(lines)


def admin_user_message(telegram_id: str, account: dict) -> str:
    jobs = recent_jobs(account, 5)
    return (
        "<b>👤 Admin user view</b>\n\n"
        f"🆔 ID: <code>{escape(telegram_id)}</code>\n"
        f"📌 Status: {escape(str(account.get('status', 'active')))}\n"
        f"💳 Balance: <b>{balance_credit(account)} credit</b>\n"
        f"💵 Deposit: {safe_int(account.get('deposit_credit'))} credit\n"
        f"🎁 Referral: {referral_credit(account)} credit\n"
        f"📥 Total deposit: {safe_int(account.get('total_deposit'))} credit\n"
        f"📤 Total spent: {total_spent(account)} credit\n"
        f"👥 Valid referrals: {safe_int(account.get('valid_invited_users'))}\n"
        f"📋 Recent jobs: {len(jobs)}"
    )


def admin_credit_prompt_message(telegram_id: str, account: dict, action: str) -> str:
    label = CREDIT_ACTION_LABELS.get(action, "Credit update")
    hint = "Send zero or a positive whole number." if action == "set" else "Send a positive whole amount."
    return (
        f"<b>{escape(label)}</b>\n\n"
        f"User: <code>{escape(telegram_id)}</code>\n"
        f"Current deposit balance: <b>{safe_int(account.get('deposit_credit'))}</b>\n\n"
        f"{hint}\n"
        "Example: <code>10</code>"
    )


def admin_credit_confirm_message(
    telegram_id: str,
    action: str,
    amount: int,
    before_balance: int,
    after_balance: int,
) -> str:
    label = CREDIT_ACTION_LABELS.get(action, "Credit update")
    return (
        f"<b>Confirm {escape(label)}</b>\n\n"
        f"User: <code>{escape(telegram_id)}</code>\n"
        f"Action: <b>{escape(label)}</b>\n"
        f"Amount: <b>{safe_int(amount)}</b>\n\n"
        f"Before deposit: <b>{safe_int(before_balance)}</b>\n"
        f"After deposit: <b>{safe_int(after_balance)}</b>"
    )


def admin_credit_history_message(telegram_id: str, account: dict) -> str:
    ledger = list(account.get("credit_ledger", []))
    if not ledger:
        return f"<b>🧾 Credit history</b>\n\nUser: <code>{escape(telegram_id)}</code>\n\nNo credit changes found."

    lines = [
        "<b>🧾 Credit history</b>",
        f"User: <code>{escape(telegram_id)}</code>",
        "",
    ]
    for entry in reversed(ledger[-10:]):
        action = str(entry.get("action", "")).lower()
        label = CREDIT_ACTION_LABELS.get(action, action.title() or "Credit")
        created_at = str(entry.get("created_at", ""))[:19].replace("T", " ")
        admin_id = str(entry.get("admin_id", ""))
        lines.append(
            f"• <b>{escape(label)}</b> {safe_int(entry.get('amount'))} "
            f"({safe_int(entry.get('before'))} → {safe_int(entry.get('after'))})"
        )
        lines.append(f"  <code>{escape(created_at)}</code> by <code>{escape(admin_id)}</code>")
    return "\n".join(lines)


def admin_recent_jobs_message(items: list[tuple[str, dict]]) -> str:
    if not items:
        return "<b>📋 Admin recent jobs</b>\n\nNo jobs found."
    lines = ["<b>📋 Admin recent jobs</b>", ""]
    for telegram_id, job in items[:10]:
        status = str(job.get("status", "PENDING")).upper()
        lines.append(
            f"{status_emoji(status)} <code>{escape(telegram_id)}</code> "
            f"{escape(str(job.get('gmail', 'unknown')))} "
            f"<code>{escape(status)}</code>"
        )
    return "\n".join(lines)
