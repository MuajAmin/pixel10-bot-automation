"""HTTP client for the remote Android worker API.

This runs on the Windows PC (alongside the Telegram bot) and dispatches
jobs to the Linux VPS where ReDroid + Worker API are running.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

ANDROID_WORKER_URL = os.getenv("ANDROID_WORKER_URL", "http://localhost:8800")
ANDROID_WORKER_API_KEY = os.getenv("ANDROID_WORKER_API_KEY", "changeme")

# Fail-fast: refuse to start with the default key
if ANDROID_WORKER_API_KEY in ("changeme", ""):
    raise RuntimeError(
        "ANDROID_WORKER_API_KEY is not configured. Set it in your .env file "
        "to match the API_KEY on the worker VPS."
    )

# Poll interval when waiting for job completion (seconds)
_POLL_INTERVAL = 5
# Maximum time to wait for a job to complete (seconds)
_MAX_WAIT = 600
# Retry config for transient HTTP errors
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2  # seconds, doubles each retry

# ── Shared HTTP session (connection pooling) ─────────────────────
_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    """Return (or create) the shared aiohttp session for connection reuse."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={
                "X-API-Key": ANDROID_WORKER_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )
    return _session


async def close_session() -> None:
    """Close the shared session — call on shutdown."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


# ── Public API ───────────────────────────────────────────────────


async def check_health() -> dict[str, Any]:
    """Check if the Android worker API is reachable and healthy."""
    try:
        session = _get_session()
        async with session.get(
            f"{ANDROID_WORKER_URL}/health",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()
            return data
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)}


async def submit_job(
    gmail: str,
    password: str,
    method: str = "device_prompt",
    totp_secret: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """Submit a new job to the Android worker API.

    Returns the initial job response (job_id, status).
    """
    payload = {
        "gmail": gmail,
        "password": password,
        "method": method,
        "totp_secret": totp_secret,
        "job_id": job_id,
    }
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            session = _get_session()
            async with session.post(
                f"{ANDROID_WORKER_URL}/jobs",
                json=payload,
            ) as resp:
                if resp.status == 409:
                    data = await resp.json()
                    return {"status": "DUPLICATE", "message": data.get("detail", "")}
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            if attempt == _MAX_RETRIES:
                logger.error("Failed to submit job after %d attempts: %s", _MAX_RETRIES, exc)
                return {"status": "ERROR", "message": str(exc)}
            
            backoff = _RETRY_BACKOFF ** attempt
            logger.warning(
                "Transient error submitting job (attempt %d/%d): %s. Retrying in %ds...",
                attempt, _MAX_RETRIES, exc, backoff
            )
            await asyncio.sleep(backoff)


async def get_job_status(job_id: str) -> dict[str, Any]:
    """Get the current status of a job."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            session = _get_session()
            async with session.get(
                f"{ANDROID_WORKER_URL}/jobs/{job_id}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:
            if attempt == _MAX_RETRIES:
                logger.error("Failed to get job status after %d attempts: %s", _MAX_RETRIES, exc)
                return {"status": "ERROR", "message": str(exc)}
            
            backoff = _RETRY_BACKOFF ** attempt
            logger.warning(
                "Transient error getting job status (attempt %d/%d): %s. Retrying in %ds...",
                attempt, _MAX_RETRIES, exc, backoff
            )
            await asyncio.sleep(backoff)


async def cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel a running job."""
    try:
        session = _get_session()
        async with session.delete(
            f"{ANDROID_WORKER_URL}/jobs/{job_id}",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            return await resp.json()
    except Exception as exc:
        return {"status": "ERROR", "message": str(exc)}


async def wait_for_job(
    job_id: str,
    progress_callback: Any = None,
    max_wait: int = _MAX_WAIT,
) -> dict[str, Any]:
    """Poll the job status until it completes or times out.

    Parameters
    ----------
    job_id : str
        Job identifier.
    progress_callback : callable, optional
        async function(percent: int, note: str) called on each poll.
    max_wait : int
        Maximum wait time in seconds.

    Returns
    -------
    dict : Final job result.
    """
    import time

    deadline = time.time() + max_wait
    terminal_states = {
        "CLAIMED", "OFFER_FOUND", "NO_OFFER",
        "LOGIN_FAILED", "ERROR", "TIMEOUT", "CANCELLED",
        "SUCCESS",
        "FAILED", "BLOCKED", "UNSAFE", "2FA", "EMAIL", "PASSWORD", "TOS", "UNKNOWN"
    }

    while time.time() < deadline:
        status = await get_job_status(job_id)

        if status.get("status") in terminal_states:
            if progress_callback:
                await progress_callback(100, status.get("message", "Done"))
            return status

        if progress_callback:
            await progress_callback(
                status.get("progress", 0),
                status.get("progress_note", "Processing..."),
            )

        await asyncio.sleep(_POLL_INTERVAL)

    return {
        "status": "TIMEOUT",
        "message": f"Job did not complete within {max_wait}s",
    }


async def run_android_job_remote(
    gmail: str,
    password: str,
    method: str = "device_prompt",
    totp_secret: str = "",
    job_id: str = "",
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Submit a job and wait for completion (convenience wrapper).

    This is the main function called by worker.py when WORKER_BACKEND=android.
    """
    # Submit
    submit_result = await submit_job(
        gmail=gmail,
        password=password,
        method=method,
        totp_secret=totp_secret,
        job_id=job_id,
    )

    if submit_result.get("status") in ("ERROR", "DUPLICATE"):
        return submit_result

    actual_job_id = submit_result.get("job_id", job_id)

    # Wait for completion
    return await wait_for_job(
        job_id=actual_job_id,
        progress_callback=progress_callback,
    )
