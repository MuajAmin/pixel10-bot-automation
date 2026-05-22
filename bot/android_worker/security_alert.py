"""Auto-approve Google security alerts on Android.

When Google detects a login from an "Unknown device" it may show a
"Suspicious sign-in attempt" security alert that requires the user to
confirm "Yes, it was me".  This module detects and auto-approves those
alerts during the login flow.

It supports two scenarios:
  1. The alert appears as part of the login WebView (inline challenge).
  2. The alert appears as a system notification that must be handled
     via the notification shade or Google Account app.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import uiautomator2 as u2

logger = logging.getLogger(__name__)

# ── Security alert detection markers ─────────────────────────────

_ALERT_SCREEN_MARKERS = [
    "suspicious sign-in",
    "verify it's you",
    "was it you",
    "yes, it was me",
    "review this activity",
    "check activity",
    "confirm it's you",
    "someone who knows your password",
    "sign-in attempt",
    "critical security alert",
]

_APPROVE_BUTTONS = [
    "Yes, it was me",
    "Yes",
    "It was me",
    "Approve",
    "Allow",
    "Confirm",
    "YES, IT WAS ME",
]

_DISMISS_BUTTONS = [
    "Got it",
    "OK",
    "Dismiss",
    "Close",
    "Done",
]


from .device import get_screen_text as _get_screen_text


async def detect_security_alert(device: u2.Device) -> bool:
    """Check if a Google security alert is visible on screen.

    Returns True if a security alert/challenge is detected.
    """
    text = await _get_screen_text(device)
    return any(marker in text for marker in _ALERT_SCREEN_MARKERS)


async def approve_security_alert(
    device: u2.Device,
    job_id: str = "",
) -> bool:
    """Attempt to auto-approve a Google security alert.

    Tries multiple button labels and interaction patterns.
    Returns True if the alert was successfully approved.
    """
    d = device
    logger.info("[%s] Attempting to auto-approve security alert", job_id)

    # ── Attempt 1: Click approval button directly ─────────────────
    for btn_text in _APPROVE_BUTTONS:
        try:
            if await asyncio.to_thread(
                lambda b=btn_text: d(textContains=b).exists(timeout=2)
            ):
                await asyncio.to_thread(
                    lambda b=btn_text: d(textContains=b).click()
                )
                logger.info(
                    "[%s] Clicked approval button: %s", job_id, btn_text,
                )
                await asyncio.sleep(3)

                # Check if alert was dismissed
                if not await detect_security_alert(device):
                    logger.info("[%s] Security alert approved", job_id)
                    return True
        except Exception:
            continue

    # ── Attempt 2: Try scrolling down to find the button ──────────
    try:
        # Some alerts require scrolling to see the approval button
        await asyncio.to_thread(
            lambda: d(scrollable=True).scroll.toEnd()
        )
        await asyncio.sleep(1)

        for btn_text in _APPROVE_BUTTONS:
            if await asyncio.to_thread(
                lambda b=btn_text: d(textContains=b).exists(timeout=2)
            ):
                await asyncio.to_thread(
                    lambda b=btn_text: d(textContains=b).click()
                )
                logger.info(
                    "[%s] Clicked approval button after scroll: %s",
                    job_id, btn_text,
                )
                await asyncio.sleep(3)
                return True
    except Exception:
        pass

    # ── Attempt 3: Check notification shade ───────────────────────
    try:
        # Open notification shade
        await asyncio.to_thread(device.open_notification)
        await asyncio.sleep(2)

        notif_text = await _get_screen_text(device)
        if any(m in notif_text for m in _ALERT_SCREEN_MARKERS):
            logger.info("[%s] Found security alert in notifications", job_id)

            # Try clicking the notification
            for marker in ["security alert", "sign-in attempt", "suspicious"]:
                if await asyncio.to_thread(
                    lambda m=marker: d(textContains=m).exists(timeout=2)
                ):
                    await asyncio.to_thread(
                        lambda m=marker: d(textContains=m).click()
                    )
                    await asyncio.sleep(3)

                    # Now try approving from the opened activity
                    for btn_text in _APPROVE_BUTTONS:
                        if await asyncio.to_thread(
                            lambda b=btn_text: d(textContains=b).exists(timeout=3)
                        ):
                            await asyncio.to_thread(
                                lambda b=btn_text: d(textContains=b).click()
                            )
                            logger.info(
                                "[%s] Approved via notification: %s",
                                job_id, btn_text,
                            )
                            await asyncio.sleep(3)
                            return True
                    break

        # Close notification shade if still open
        await asyncio.to_thread(lambda: d.press("back"))
        await asyncio.sleep(1)
    except Exception as exc:
        logger.debug("[%s] Notification check failed: %s", job_id, exc)

    logger.warning("[%s] Could not auto-approve security alert", job_id)
    return False


async def handle_security_alert_flow(
    device: u2.Device,
    gmail: str,
    job_id: str = "",
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Full security alert handling flow with retries.

    Returns a dict with:
        handled: bool — whether the alert was successfully handled
        attempts: int — number of attempts made
        message: str — human-readable status
    """
    result: dict[str, Any] = {
        "handled": False,
        "attempts": 0,
        "message": "",
    }

    for attempt in range(1, max_attempts + 1):
        result["attempts"] = attempt

        # Check if there's actually an alert
        if not await detect_security_alert(device):
            result["handled"] = True
            result["message"] = "No security alert detected"
            return result

        logger.info(
            "[%s] Security alert detected (attempt %d/%d)",
            job_id, attempt, max_attempts,
        )

        # Try to approve
        approved = await approve_security_alert(device, job_id)
        if approved:
            # Verify it's actually gone
            await asyncio.sleep(2)
            if not await detect_security_alert(device):
                result["handled"] = True
                result["message"] = f"Security alert approved (attempt {attempt})"
                return result

        # Wait before retry
        await asyncio.sleep(3)

    result["message"] = (
        f"Could not auto-approve security alert after {max_attempts} attempts. "
        f"Account {gmail} may need manual approval at "
        "myaccount.google.com/notifications"
    )
    return result


__all__ = [
    "detect_security_alert",
    "approve_security_alert",
    "handle_security_alert_flow",
]
