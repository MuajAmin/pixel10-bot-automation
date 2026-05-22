"""Login worker manager — job lifecycle, concurrency tracking, and cleanup.

This module orchestrates native Android verification jobs with production-grade
job management:

  • Prevents duplicate jobs for the same Gmail address.
  • Tracks in-flight asyncio Tasks and exposes helpers for admin inspection.
  • Enforces a per-user concurrency limit (default 1 job at a time).
  • Provides graceful cancellation and cleanup on shutdown.
  • Supports Android ReDroid backend.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Backend type — currently only "android" (ReDroid) is supported.
WORKER_BACKEND = os.getenv("WORKER_BACKEND", "android")

# ── In-flight job registry ───────────────────────────────────────────


# job_id → {"task": asyncio.Task, "gmail": str, "telegram_id": str, "started": float}
_active_jobs: dict[str, dict[str, Any]] = {}

# Per-user concurrency limit.  Set to 0 for unlimited.
MAX_CONCURRENT_PER_USER = 1


# ── Public API ───────────────────────────────────────────────────────

def start_login_job(
    gmail: str,
    password: str,
    method: str,
    job_id: str,
    telegram_id: str,
    bot: Any,
    chat_id: int,
    message_id: int | None = None,
    charged: int = 0,
    credit_source: str = "",
) -> asyncio.Task:
    """Schedule a login job with concurrency and duplicate guards.

    Raises
    ------
    RuntimeError
        If the user already has ``MAX_CONCURRENT_PER_USER`` jobs in flight,
        or if a job for the same Gmail address is already running.
    """

    _gc_finished_jobs()

    gmail_lower = gmail.strip().lower()

    # ── Guard: duplicate Gmail ────────────────────────────────────────
    for jid, meta in _active_jobs.items():
        if meta["gmail"] == gmail_lower and not meta["task"].done():
            raise RuntimeError(
                f"A job for {gmail_lower} is already running (job {jid})."
            )

    # ── Guard: per-user concurrency ───────────────────────────────────
    if MAX_CONCURRENT_PER_USER > 0:
        user_running = sum(
            1
            for meta in _active_jobs.values()
            if meta["telegram_id"] == telegram_id and not meta["task"].done()
        )
        if user_running >= MAX_CONCURRENT_PER_USER:
            raise RuntimeError(
                f"You already have {user_running} job(s) running. "
                f"Maximum concurrent jobs per user is {MAX_CONCURRENT_PER_USER}."
            )

    # ── Dispatch to the remote Android backend ────────────────────────
    task = asyncio.create_task(
        _run_android_job(
            gmail=gmail,
            password=password,
            method=method,
            job_id=job_id,
            telegram_id=telegram_id,
            bot=bot,
            chat_id=chat_id,
            message_id=message_id,
            charged=charged,
            credit_source=credit_source,
        )
    )
    logger.info("Job %s dispatched to remote ANDROID backend", job_id)

    _active_jobs[job_id] = {
        "task": task,
        "gmail": gmail_lower,
        "telegram_id": telegram_id,
        "started": time.time(),
    }

    # Auto-remove from registry when done (fire-and-forget callback).
    task.add_done_callback(lambda _t: _on_job_done(job_id))
    logger.info(
        "Job %s scheduled for %s (user %s) — %d active [backend=%s]",
        job_id, gmail_lower, telegram_id, active_job_count(), WORKER_BACKEND,
    )
    return task


def cancel_job(job_id: str) -> bool:
    """Cancel a running job by job_id.  Returns True if cancelled."""
    meta = _active_jobs.get(job_id)
    if not meta:
        return False
    task = meta["task"]
    if task.done():
        return False
    task.cancel()
    logger.info("Job %s cancelled", job_id)
    return True


def active_job_count() -> int:
    """Return the number of currently in-flight jobs."""
    _gc_finished_jobs()
    return sum(1 for meta in _active_jobs.values() if not meta["task"].done())


def active_jobs_for_user(telegram_id: str) -> list[str]:
    """Return job_ids for a user's in-flight jobs."""
    _gc_finished_jobs()
    return [
        jid
        for jid, meta in _active_jobs.items()
        if meta["telegram_id"] == telegram_id and not meta["task"].done()
    ]


def active_jobs_summary() -> list[dict[str, Any]]:
    """Return a list of summary dicts for all active jobs (admin inspection)."""
    _gc_finished_jobs()
    now = time.time()
    return [
        {
            "job_id": jid,
            "gmail": meta["gmail"],
            "telegram_id": meta["telegram_id"],
            "elapsed_s": round(now - meta["started"], 1),
            "done": meta["task"].done(),
        }
        for jid, meta in _active_jobs.items()
        if not meta["task"].done()
    ]


async def shutdown_all(timeout: float = 30.0) -> int:
    """Cancel every running job and wait up to *timeout* seconds.

    Called during bot shutdown to ensure clean browser cleanup.
    Returns the number of jobs that were cancelled.
    """
    _gc_finished_jobs()
    running = [
        (jid, meta["task"])
        for jid, meta in _active_jobs.items()
        if not meta["task"].done()
    ]
    if not running:
        return 0

    logger.info("Shutting down %d active job(s)…", len(running))
    for jid, task in running:
        task.cancel()

    tasks = [task for _, task in running]
    await asyncio.gather(*tasks, return_exceptions=True)
    _active_jobs.clear()
    logger.info("All jobs shut down.")
    return len(running)


# ── Android backend helper ───────────────────────────────────────────


async def _run_android_job(
    gmail: str,
    password: str,
    method: str,
    job_id: str,
    telegram_id: str,
    bot: Any,
    chat_id: int,
    message_id: int | None = None,
    charged: int = 0,
    credit_source: str = "",
) -> None:
    """Dispatch a job to the remote Android worker API and relay progress.

    This coroutine submits the job via HTTP, polls for status, and sends
    Telegram progress messages back to the user.  It also persists the
    final result to account/job storage and auto-refunds on failure.
    """
    from bot.accounts import refund_job, update_job_status
    from bot.android_worker.client import run_android_job_remote

    # ── Translate Telegram-layer method to worker protocol ────────
    # Telegram sends: "Verify sign-in" or "2FA Secret:<base32>"
    # Worker expects: method="device_prompt"|"totp", totp_secret="<base32>"
    totp_secret = ""
    if method.startswith("2FA Secret:"):
        totp_secret = method.removeprefix("2FA Secret:").strip()
        worker_method = "totp"
    elif method in ("Verify sign-in", "device_prompt"):
        worker_method = "device_prompt"
    else:
        # Fallback — pass through as-is for forward compatibility
        worker_method = method

    async def _progress(percent: int, note: str) -> None:
        """Relay progress updates to Telegram."""
        try:
            if bot and chat_id and message_id:
                text = (
                    f"⚙️ Job details\n\n"
                    f"🆔 Job ID: {job_id}\n"
                    f"📧 Email: {gmail}\n"
                    f"⚙️ Status: PROCESSING\n"
                    f"💸 Charged: {charged} credit\n"
                    f"🏷️ Credit source: {credit_source}\n"
                    f"📈 Progress: {percent}%\n"
                    f"📝 Progress note: {note}"
                )
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                )
        except Exception:
            pass  # Telegram edit failures are non-critical

    # VPN routing is maintained by the host-level vpn_keeper.sh daemon
    logger.info("VPN routing maintained by host daemon for job %s", job_id)

    try:
        result = await run_android_job_remote(
            gmail=gmail,
            password=password,
            method=worker_method,
            totp_secret=totp_secret,
            job_id=job_id,
            progress_callback=_progress,
        )

        # Send final result to user
        status = result.get("status", "ERROR")
        offer_url = result.get("offer_url", "")
        message = result.get("message", "")

        if status == "CLAIMED" and offer_url:
            final_status = "SUCCEEDED"
            final_text = (
                f"✅ Job details\n\n"
                f"🆔 Job ID: {job_id}\n"
                f"📧 Email: {gmail}\n"
                f"✅ Status: SUCCEEDED\n"
                f"💸 Charged: {charged} credit\n"
                f"🏷️ Credit source: {credit_source}\n"
                f"📈 Progress: 100%\n"
                f"📝 Progress note: Offer claimed successfully\n"
                f"🔗 Redeem link: {offer_url}"
            )
        elif status == "OFFER_FOUND":
            # Offer detected but NOT confirmed claimed — report honestly
            final_status = "COMPLETED"
            note = f"Offer found but claim not confirmed"
            if offer_url:
                note += f": {offer_url}"
            final_text = (
                f"🔍 Job details\n\n"
                f"🆔 Job ID: {job_id}\n"
                f"📧 Email: {gmail}\n"
                f"🔍 Status: OFFER_FOUND\n"
                f"💸 Charged: {charged} credit\n"
                f"🏷️ Credit source: {credit_source}\n"
                f"📈 Progress: 100%\n"
                f"📝 Progress note: {note}"
            )
            if offer_url:
                final_text += f"\n🔗 Offer link: {offer_url}"
        elif status == "NO_OFFER":
            final_status = "COMPLETED"
            final_text = (
                f"✅ Job details\n\n"
                f"🆔 Job ID: {job_id}\n"
                f"📧 Email: {gmail}\n"
                f"✅ Status: COMPLETED\n"
                f"💸 Charged: {charged} credit\n"
                f"🏷️ Credit source: {credit_source}\n"
                f"📈 Progress: 100%\n"
                f"📝 Progress note: {message}"
            )
        else:
            final_status = "FAILED"
            final_text = (
                f"⚠️ Job details\n\n"
                f"🆔 Job ID: {job_id}\n"
                f"📧 Email: {gmail}\n"
                f"⚠️ Status: {status}\n"
                f"💸 Charged: {charged} credit\n"
                f"🏷️ Credit source: {credit_source}\n"
                f"📈 Progress: 100%\n"
                f"📝 Progress note: {message}"
            )

        # ── Persist result to account/job storage ─────────────────
        extra: dict[str, Any] = {
            "progress": 100,
            "progress_note": message or "Done",
        }
        if offer_url:
            extra["redeem_link"] = offer_url
        await update_job_status(telegram_id, job_id, final_status, extra)

        # Auto-refund on failure
        if final_status == "FAILED":
            refunded = await refund_job(telegram_id, job_id)
            if refunded:
                final_text += "\n\n💰 Credit has been auto-refunded."
                logger.info("Job %s auto-refunded for user %s", job_id, telegram_id)

        if bot and chat_id and message_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=final_text,
                )
            except Exception:
                pass

    except Exception as exc:
        logger.exception("Android job %s failed: %s", job_id, exc)

        # ── Persist failure to storage ────────────────────────────
        await update_job_status(
            telegram_id,
            job_id,
            "FAILED",
            {"progress": 100, "progress_note": str(exc)[:200], "error": str(exc)[:200]},
        )
        refunded = await refund_job(telegram_id, job_id)
        refund_note = " Credit has been auto-refunded." if refunded else ""

        if bot and chat_id and message_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        f"❌ Job details\n\n"
                        f"🆔 Job ID: {job_id}\n"
                        f"📧 Email: {gmail}\n"
                        f"❌ Status: FAILED\n"
                        f"💸 Charged: {charged} credit\n"
                        f"🏷️ Credit source: {credit_source}\n"
                        f"📈 Progress: 100%\n"
                        f"📝 Progress note: {exc}"
                        f"{refund_note}"
                    ),
                )
            except Exception:
                pass


# ── Internal helpers ─────────────────────────────────────────────────

def _on_job_done(job_id: str) -> None:
    """Callback fired when a job task completes."""
    meta = _active_jobs.get(job_id)
    if meta:
        elapsed = time.time() - meta["started"]
        task = meta["task"]
        if task.cancelled():
            logger.info("Job %s was cancelled after %.1fs", job_id, elapsed)
        elif task.exception():
            logger.warning(
                "Job %s crashed after %.1fs: %s",
                job_id, elapsed, task.exception(),
            )
        else:
            logger.info("Job %s completed in %.1fs", job_id, elapsed)


def _gc_finished_jobs() -> None:
    """Remove completed/cancelled jobs older than 5 minutes from the registry."""
    cutoff = time.time() - 300
    stale = [
        jid
        for jid, meta in _active_jobs.items()
        if meta["task"].done() and meta["started"] < cutoff
    ]
    for jid in stale:
        del _active_jobs[jid]


__all__ = [
    "start_login_job",
    "cancel_job",
    "active_job_count",
    "active_jobs_for_user",
    "active_jobs_summary",
    "shutdown_all",
]
