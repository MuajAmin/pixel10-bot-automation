"""Google One app — Pixel offer detection and claiming on Android.

This module opens the Google One app natively on the ReDroid container,
navigates to Benefits / Settings, checks for offers, and claims them.
Unlike the browser-based approach, the native app has access to Play
Services device registration, which makes Pixel-specific offers visible.

All UI interactions are routed through HumanInteractor from humanize.py
for realistic delays, Bezier swipes, and offset taps.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from typing import Any

import uiautomator2 as u2

from .config import PKG_GEMINI, PKG_GOOGLE_ONE
from .device import launch_app, stop_app, take_screenshot
from .humanize import HumanInteractor, create_human

logger = logging.getLogger(__name__)

# ── Offer detection markers ──────────────────────────────────────

_OFFER_MARKERS = [
    "redeem offer",
    "claim your",
    "activate offer",
    "pixel offer",
    "gemini advanced",
    "ai premium",
    "google one ai",
    "included with pixel",
    "pixel benefit",
    "months free with pixel",
    "included at no charge",
]

_NO_OFFER_MARKERS = [
    "no offers available",
    "you're all set",
    "no new offers",
    "check back later",
]

_CLAIM_SUCCESS_MARKERS = [
    "you're all set",
    "subscription started",
    "successfully activated",
    "enjoy your subscription",
    "trial activated",
    "successfully redeemed",
]
_APP_CONSTRAINT_MARKERS = {
    "offer_not_available": [
        "offer not available",
        "this offer isn't available",
        "this offer is not available",
        "no longer available",
    ],
    "family_group_validation_restriction": [
        "family group",
        "family manager",
        "family validation",
        "not available for family members",
    ],
    "account_profile_country_mismatch": [
        "country mismatch",
        "not available in your country",
        "profile country",
        "payments profile country",
        "change your country",
    ],
}
_VERIFY_NUMBER_RE = re.compile(
    r"\btap\s+([0-9])\s*([0-9])\s+on\s+your\s+phone\b",
    re.IGNORECASE,
)


from .device import get_screen_text as _screen_text


async def _screen_dump(device: u2.Device) -> str:
    """Get raw XML hierarchy dump."""
    try:
        return await asyncio.to_thread(device.dump_hierarchy)
    except Exception:
        return ""


def _normalize_layout_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_verification_number(raw_layout: str) -> str:
    match = _VERIFY_NUMBER_RE.search(_normalize_layout_text(raw_layout))
    if not match:
        return ""
    return f"{match.group(1)}{match.group(2)}"


async def _extract_urls_from_screen(device: u2.Device) -> list[str]:
    """Extract offer/redeem URLs from multiple Android sources.

    Native apps don't expose URLs as visible text, so we must check:
    1. UI hierarchy XML dump (works for WebViews / displayed URLs)
    2. Activity intents and recent tasks (browser/CustomTab launches)
    3. Logcat — Google One & Play Services log offer URLs
    4. Clipboard (some flows copy the link)
    """
    urls: list[str] = []
    url_pattern = r'https?://[^\s"<>\');\]]+' 

    # ── Method 1: UI hierarchy XML dump ──────────────────────────
    try:
        dump = await _screen_dump(device)
        found = re.findall(url_pattern, dump)
        urls.extend(found)
    except Exception:
        pass

    # ── Method 2: Activity intents / recent tasks ────────────────
    # Catches URLs when Google One opens a Chrome Custom Tab or browser.
    # Run raw dumpsys commands and filter in Python for shell portability.
    intent_cmds = [
        "dumpsys activity activities",
        "dumpsys activity recents",
        "dumpsys activity starter",
    ]
    for cmd in intent_cmds:
        try:
            result = await asyncio.to_thread(device.shell, cmd)
            output = result.output if hasattr(result, 'output') else str(result)
            if output:
                found = re.findall(url_pattern, output)
                # Filter to Google One URLs in Python
                urls.extend(
                    u for u in found
                    if "one.google.com" in u
                )
        except Exception:
            pass

    # ── Method 3: Logcat — Google One / Play Services URL logs ───
    # Fetch logcat and filter in Python instead of using grep pipelines.
    try:
        result = await asyncio.to_thread(
            device.shell,
            "logcat -d -s GoogleOne:* GmsOffers:* PlayOffers:* "
            "chromium:* CustomTabs:*"
        )
        output = result.output if hasattr(result, 'output') else str(result)
        if output:
            found = re.findall(url_pattern, output)
            urls.extend(
                u for u in found
                if "one.google.com" in u
            )
    except Exception:
        pass

    # Broader logcat sweep for offer/redeem patterns
    try:
        result = await asyncio.to_thread(device.shell, "logcat -d")
        output = result.output if hasattr(result, 'output') else str(result)
        if output:
            # Filter to offer URLs in Python
            found = re.findall(
                r'https://one\.google\.com/offer/[A-Za-z0-9_?=&hl]+',
                output,
            )
            urls.extend(found)
    except Exception:
        pass

    # ── Method 4: Clipboard content ──────────────────────────────
    try:
        result = await asyncio.to_thread(
            device.shell,
            "su -c 'service call clipboard 2 i32 1 i32 1' 2>/dev/null || "
            "am broadcast -a clipper.get 2>/dev/null"
        )
        if result.output:
            found = re.findall(url_pattern, result.output)
            urls.extend(found)
    except Exception:
        pass

    # ── Deduplicate & filter ─────────────────────────────────────
    # ONLY return validated Google One offer/redeem URLs.
    # Never fall back to arbitrary URLs — that causes false positives.
    # Preserve insertion order (UI dump → intents → logcat → clipboard)
    # so the most reliable/recent source wins deterministically.
    unique_urls = list(dict.fromkeys(urls))  # dedup preserving order
    offer_urls = [
        u for u in unique_urls
        if "one.google.com/offer" in u
        or "one.google.com/redeem" in u
    ]
    return offer_urls


def _detect_application_constraint(text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    for code, markers in _APP_CONSTRAINT_MARKERS.items():
        for marker in markers:
            if marker in lowered:
                return code, marker
    return None


async def _monitor_verification_number(
    device: u2.Device,
    job_id: str,
    progress_callback: Any = None,
) -> None:
    """Poll active hierarchy and dispatch 2-digit phone-tap challenges."""
    seen: set[str] = set()
    while True:
        try:
            dump = await asyncio.wait_for(_screen_dump(device), timeout=8.0)
        except asyncio.TimeoutError:
            logger.warning("[%s] Verification monitor hierarchy dump timed out", job_id)
            await asyncio.sleep(2.0)
            continue

        code = _extract_verification_number(dump or "")
        if code:
            if code not in seen:
                seen.add(code)
                logger.info("[%s] Verification phone-tap number detected: %s", job_id, code)
                if progress_callback:
                    await progress_callback(45, f"Tap {code} on your phone to verify sign-in")
        await asyncio.sleep(1.0)


# ── Main Offer Claim Flow ────────────────────────────────────────


async def claim_pixel_offer(
    device: u2.Device,
    gmail: str,
    job_id: str = "",
    human_profile: str = "normal",
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Open Google One app and attempt to find and claim a Pixel offer.

    All interactions use HumanInteractor for realistic delays and offsets.

    Strategy:
    1. Open Google One app → check Benefits tab
    2. Go to Settings → "Check for offers"
    3. Try Gemini app as fallback
    4. Extract the offer/redeem link
    5. Click through the claim flow

    Returns
    -------
    dict with keys:
        status: CLAIMED | OFFER_FOUND | NO_OFFER | ERROR
        offer_url: The redeem/offer URL (if found)
        offer_type: "pixel_specific" | "account_discount" | "gemini" | ""
        message: Human-readable description
        screenshots: list of screenshot paths
    """
    result: dict[str, Any] = {
        "status": "NO_OFFER",
        "offer_url": "",
        "offer_type": "",
        "message": "",
        "screenshots": [],
    }

    # Initialize HumanInteractor for all UI interactions
    human = create_human(device, profile=human_profile)
    monitor_task = asyncio.create_task(
        _monitor_verification_number(device, job_id, progress_callback)
    )

    try:
        # ── Clear stale sources before scanning ──────────────────
        # Logcat and clipboard may contain URLs from previous jobs.
        logger.info("[%s] Clearing logcat & clipboard to prevent stale URL leakage", job_id)
        try:
            await asyncio.to_thread(device.shell, "logcat -c")
        except Exception:
            pass
        try:
            # Clear clipboard via broadcast (best-effort)
            await asyncio.to_thread(
                device.shell,
                "am broadcast -a clipper.set -e text '' 2>/dev/null || true",
            )
        except Exception:
            pass

        # ── Strategy 1: Google One App → Benefits ────────────────
        logger.info("[%s] Opening Google One app...", job_id)
        await launch_app(device, PKG_GOOGLE_ONE)
        await human.sleep(5.0)
        # Brief idle behavior while app loads
        await human.idle_behavior(duration=1.5)

        ss = await take_screenshot(device, "06_google_one_home", job_id)
        if ss:
            result["screenshots"].append(ss)

        # Dismiss any popups (humanized)
        await human.dismiss_dialogs()
        await human.sleep(1.0)

        # Check Benefits tab
        benefits_found = await _try_benefits_tab(device, job_id, result, human=human)
        if result["status"] == "APP_CONSTRAINT":
            return result

        # ── Strategy 2: Settings → Check for offers ──────────────
        if not benefits_found:
            logger.info("[%s] Trying Settings → Check for offers", job_id)
            await _try_settings_check(device, job_id, result, human=human)
            if result["status"] == "APP_CONSTRAINT":
                return result

        # ── Strategy 3: Direct offer URL navigation ──────────────
        # Only use extracted URLs as supplementary evidence — do NOT
        # promote to OFFER_FOUND solely from URL extraction without
        # UI marker confirmation (prevents stale/unrelated URL hits).
        if result["status"] == "OFFER_FOUND" and not result["offer_url"]:
            logger.info("[%s] Trying URL extraction to find offer link", job_id)
            urls = await _extract_urls_from_screen(device)
            if urls:
                result["offer_url"] = urls[0]
                logger.info("[%s] Attached offer URL: %s", job_id, urls[0])

        # ── Strategy 4: Gemini App ───────────────────────────────
        if result["status"] == "NO_OFFER":
            logger.info("[%s] Trying Gemini app...", job_id)
            await _try_gemini_app(device, job_id, result, human=human)

        # ── Attempt to claim if offer found ──────────────────────
        if result["status"] in ("OFFER_FOUND",):
            logger.info("[%s] Attempting to claim offer...", job_id)
            # Clear logcat before claim so we get a clean signal
            try:
                await asyncio.to_thread(device.shell, "logcat -c")
            except Exception:
                pass

            claimed = await _click_claim_buttons(device, job_id, result, human=human)
            if claimed:
                result["status"] = "CLAIMED"
            elif result["status"] == "APP_CONSTRAINT":
                return result

        # ── Final URL extraction sweep ───────────────────────────
        # After the claim flow, try to extract the offer URL — but ONLY
        # save validated one.google.com offer/redeem links.
        if not result["offer_url"]:
            logger.info("[%s] Final URL extraction sweep...", job_id)
            await human.sleep(2.0)  # Let intents settle
            final_urls = await _extract_urls_from_screen(device)
            if final_urls:
                # _extract_urls_from_screen already filters to valid offer URLs
                result["offer_url"] = final_urls[0]
                logger.info("[%s] Final sweep found URL: %s", job_id, result["offer_url"])

        ss = await take_screenshot(device, "09_final_result", job_id)
        if ss:
            result["screenshots"].append(ss)

        if result["status"] == "NO_OFFER":
            result["message"] = "No Pixel/Gemini offer found for this account"
        elif result["status"] == "OFFER_FOUND":
            result["message"] = f"Offer found but not claimed: {result['offer_url']}"
        elif result["status"] == "CLAIMED":
            result["message"] = f"Offer claimed successfully: {result['offer_url']}"

    except Exception as exc:
        logger.exception("[%s] Offer claim error: %s", job_id, exc)
        result["status"] = "ERROR"
        result["message"] = str(exc)
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    return result


# ── Helper Functions ─────────────────────────────────────────────


async def _dismiss_popups(
    device: u2.Device,
    human: HumanInteractor | None = None,
) -> None:
    """Dismiss common Google One popups with humanized taps."""
    if human is None:
        human = create_human(device)
    await human.dismiss_dialogs()


async def _try_benefits_tab(
    device: u2.Device,
    job_id: str,
    result: dict[str, Any],
    human: HumanInteractor | None = None,
) -> bool:
    """Navigate to Benefits tab with humanized taps and look for offers."""
    if human is None:
        human = create_human(device)

    # Try clicking Benefits tab (humanized)
    benefits_labels = ["Benefits", "Offers", "Perks"]
    for label in benefits_labels:
        tapped = await human.tap_text(label, timeout=3, contains=False)
        if tapped:
            await human.sleep(3.0)
            break

    ss = await take_screenshot(device, "07_benefits_tab", job_id)
    if ss:
        result["screenshots"].append(ss)

    text = await _screen_text(device)
    constraint = _detect_application_constraint(text)
    if constraint:
        code, marker = constraint
        logger.warning("[%s] Application constraint on Benefits tab: %s (%s)", job_id, code, marker)
        result["status"] = "APP_CONSTRAINT"
        result["message"] = code
        ss = await take_screenshot(device, "app_constraint_benefits", job_id)
        if ss:
            result["screenshots"].append(ss)
        return False

    # Check for offers
    if any(m in text for m in _NO_OFFER_MARKERS):
        logger.info("[%s] No offers on Benefits tab", job_id)
        return False

    if any(m in text for m in _OFFER_MARKERS):
        logger.info("[%s] Offer detected on Benefits tab!", job_id)
        result["status"] = "OFFER_FOUND"

        # Try to determine offer type
        if "pixel" in text or "gemini advanced" in text or "ai premium" in text:
            result["offer_type"] = "pixel_specific"
        else:
            result["offer_type"] = "account_discount"

        # Extract URLs
        urls = await _extract_urls_from_screen(device)
        offer_urls = [u for u in urls if "/offer/" in u or "redeem" in u]
        if offer_urls:
            result["offer_url"] = offer_urls[0]

        return True

    return False


async def _try_settings_check(
    device: u2.Device,
    job_id: str,
    result: dict[str, Any],
    human: HumanInteractor | None = None,
) -> None:
    """Go to Google One Settings → Check for offers (humanized)."""
    if human is None:
        human = create_human(device)
    d = device

    # Navigate to Settings in Google One app (humanized)
    settings_selectors = [
        {"description": "Settings"},
        {"text": "Settings"},
        {"description": "More options"},
    ]
    for sel in settings_selectors:
        try:
            selector = d(**sel)
            tapped = await human.tap_element(selector, timeout=3)
            if tapped:
                await human.sleep(2.0)
                break
        except Exception:
            continue

    # Look for "Check for offers" or "Check for membership" (humanized)
    check_labels = [
        "Check for offers",
        "Check for membership",
        "Check eligibility",
    ]
    for label in check_labels:
        try:
            tapped = await human.tap_text(label, timeout=3, contains=False)
            if tapped:
                logger.info("[%s] Humanized tap on '%s'", job_id, label)
                await human.sleep(5.0)

                ss = await take_screenshot(device, "08_check_offers", job_id)
                if ss:
                    result["screenshots"].append(ss)

                text = await _screen_text(device)
                constraint = _detect_application_constraint(text)
                if constraint:
                    code, marker = constraint
                    logger.warning("[%s] Application constraint in settings check: %s (%s)", job_id, code, marker)
                    result["status"] = "APP_CONSTRAINT"
                    result["message"] = code
                    ss = await take_screenshot(device, "app_constraint_settings", job_id)
                    if ss:
                        result["screenshots"].append(ss)
                    return
                if any(m in text for m in _OFFER_MARKERS):
                    result["status"] = "OFFER_FOUND"
                    if "pixel" in text or "gemini" in text or "ai premium" in text:
                        result["offer_type"] = "pixel_specific"
                    else:
                        result["offer_type"] = "account_discount"

                    urls = await _extract_urls_from_screen(device)
                    offer_urls = [u for u in urls if "/offer/" in u or "redeem" in u]
                    if offer_urls:
                        result["offer_url"] = offer_urls[0]
                    return
        except Exception:
            continue


async def _try_gemini_app(
    device: u2.Device,
    job_id: str,
    result: dict[str, Any],
    human: HumanInteractor | None = None,
) -> None:
    """Check Gemini app for AI Premium offer (humanized)."""
    if human is None:
        human = create_human(device)
    d = device
    try:
        await stop_app(device, PKG_GOOGLE_ONE)
        await launch_app(device, PKG_GEMINI)
        await human.sleep(5.0)

        await human.dismiss_dialogs()
        await human.sleep(1.0)

        text = await _screen_text(device)

        # Look for upgrade/offer buttons in Gemini — require Pixel-specific markers
        pixel_gemini_markers = ["gemini advanced", "ai premium", "google one ai", "included with pixel"]
        if any(m in text for m in pixel_gemini_markers):
            logger.info("[%s] Gemini Pixel offer markers found", job_id)

            # Try clicking the upgrade option (humanized)
            tapped = await human.wait_and_tap(
                ["Try Gemini Advanced", "Get AI Premium", "Claim your"],
                timeout=5, contains=True,
            )
            if tapped:
                await human.sleep(3.0)

            ss = await take_screenshot(device, "08_gemini_offer", job_id)
            if ss:
                result["screenshots"].append(ss)

            text = await _screen_text(device)
            constraint = _detect_application_constraint(text)
            if constraint:
                code, marker = constraint
                logger.warning("[%s] Application constraint in Gemini app: %s (%s)", job_id, code, marker)
                result["status"] = "APP_CONSTRAINT"
                result["message"] = code
                return
            if any(m in text for m in _OFFER_MARKERS):
                result["status"] = "OFFER_FOUND"
                result["offer_type"] = "gemini"
                urls = await _extract_urls_from_screen(device)
                if urls:
                    result["offer_url"] = urls[0]

    except Exception as exc:
        logger.debug("[%s] Gemini app check failed: %s", job_id, exc)
    finally:
        await stop_app(device, PKG_GEMINI)


async def _click_claim_buttons(
    device: u2.Device,
    job_id: str,
    result: dict[str, Any],
    human: HumanInteractor | None = None,
) -> bool:
    """Click through the claim/redeem flow with humanized taps."""
    if human is None:
        human = create_human(device)
    d = device

    # Only use offer-specific claim buttons
    claim_buttons = [
        "Redeem",
        "Claim offer",
        "Claim your offer",
        "Accept and continue",
    ]

    max_steps = 8
    for step in range(max_steps):
        text = await _screen_text(device)
        constraint = _detect_application_constraint(text)
        if constraint:
            code, marker = constraint
            logger.warning("[%s] Application constraint during claim: %s (%s)", job_id, code, marker)
            result["status"] = "APP_CONSTRAINT"
            result["message"] = code
            ss = await take_screenshot(device, f"app_constraint_claim_{step}", job_id)
            if ss:
                result["screenshots"].append(ss)
            return False

        # Check if we've reached success
        if any(m in text for m in _CLAIM_SUCCESS_MARKERS):
            logger.info("[%s] Claim successful at step %d!", job_id, step)

            urls = await _extract_urls_from_screen(device)
            if urls and not result["offer_url"]:
                result["offer_url"] = urls[0]

            ss = await take_screenshot(device, f"claim_success_{step}", job_id)
            if ss:
                result["screenshots"].append(ss)
            return True

        # Try clicking each button (humanized)
        clicked = False
        for btn in claim_buttons:
            try:
                sel = d(text=btn)
                if await asyncio.to_thread(lambda: sel.exists(timeout=2)):
                    logger.info("[%s] Humanized tap on claim button: '%s' (step %d)", job_id, btn, step)
                    prev_text = text
                    # Read the button label before tapping (like a human would)
                    await human.think(min_s=0.5, max_s=1.5)
                    await human.tap_element(sel, timeout=2)
                    await human.sleep(3.0)

                    # Verify page changed (avoid click loop!)
                    new_text = await _screen_text(device)
                    if new_text[:200] == prev_text[:200]:
                        logger.debug("[%s] Page unchanged after '%s', trying next", job_id, btn)
                        continue

                    clicked = True

                    ss = await take_screenshot(device, f"claim_step_{step}", job_id)
                    if ss:
                        result["screenshots"].append(ss)
                    break
            except Exception:
                continue

        if not clicked:
            logger.info("[%s] No more claim buttons found at step %d", job_id, step)
            break

    return False
