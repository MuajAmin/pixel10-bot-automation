"""Google account login on Android via Settings → Add Account.

This replaces the Playwright-based browser login with a native Android
login flow that goes through Google Play Services, which is the key
difference that triggers device registration for Pixel offers.

All UI interactions are routed through the HumanInteractor from
humanize.py to produce randomized delays, Bezier swipes, and
offset taps that defeat Google's behavioral bot detection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import uiautomator2 as u2

from .config import LOGIN_STEP_TIMEOUT, METHOD_DEVICE_PROMPT, METHOD_TOTP, PKG_SETTINGS
from .device import launch_app, take_screenshot
from .humanize import HumanInteractor, create_human

logger = logging.getLogger(__name__)

# ── State detection markers ──────────────────────────────────────

_EMAIL_MARKERS = ["sign in", "email or phone", "enter your email"]
_PASSWORD_MARKERS = ["enter your password", "welcome"]
_2FA_PROMPT_MARKERS = ["2-step verification", "check your phone", "tap yes"]
_TOTP_MARKERS = ["enter code", "authenticator", "verification code", "6-digit"]
_SUCCESS_MARKERS = ["account added", "you're signed in", "backup", "google services"]
_TOS_MARKERS = ["i agree", "agree", "accept"]
_UNSAFE_MARKERS = ["couldn't sign you in", "this browser or app may not be secure"]
_WRONG_PASSWORD_MARKERS = ["wrong password", "incorrect password"]
_SECURITY_ALERT_MARKERS = [
    "suspicious sign-in", "verify it's you", "was it you",
    "yes, it was me", "review this activity", "confirm it's you",
    "critical security alert", "sign-in attempt",
]


from .device import get_screen_text as _get_screen_text


async def _wait_for_element(
    device: u2.Device,
    timeout: int = 10,
    **kwargs: Any,
) -> bool:
    """Wait for a UI element to appear."""
    try:
        return await asyncio.to_thread(
            lambda: device(**kwargs).exists(timeout=timeout)
        )
    except Exception:
        return False


async def _detect_login_state(device: u2.Device) -> str:
    """Detect the current state of the Google login flow.

    Returns one of:
        EMAIL, PASSWORD, 2FA_PROMPT, TOTP, SUCCESS,
        TOS, UNSAFE, WRONG_PASSWORD, UNKNOWN
    """
    text = await _get_screen_text(device)

    if any(m in text for m in _UNSAFE_MARKERS):
        return "UNSAFE"
    if any(m in text for m in _WRONG_PASSWORD_MARKERS):
        return "WRONG_PASSWORD"
    if any(m in text for m in _SUCCESS_MARKERS):
        return "SUCCESS"
    if any(m in text for m in _TOS_MARKERS) and "google" in text:
        return "TOS"
    if any(m in text for m in _SECURITY_ALERT_MARKERS):
        return "SECURITY_ALERT"
    if any(m in text for m in _TOTP_MARKERS):
        return "TOTP"
    if any(m in text for m in _2FA_PROMPT_MARKERS):
        return "2FA_PROMPT"
    if any(m in text for m in _PASSWORD_MARKERS):
        return "PASSWORD"
    if any(m in text for m in _EMAIL_MARKERS):
        return "EMAIL"

    return "UNKNOWN"


async def _type_text_human(
    device: u2.Device,
    text: str,
    human: HumanInteractor | None = None,
) -> None:
    """Type text with realistic human-like delays, typos, and variable speed.

    Routes through HumanInteractor for per-character delays with
    Gaussian timing, QWERTY-based typo simulation, and word-boundary pauses.
    """
    if human is None:
        human = create_human(device)
    await human.type_text(text, clear_first=True)


# ── Main Login Flow ──────────────────────────────────────────────


async def login_google_account(
    device: u2.Device,
    gmail: str,
    password: str,
    method: str = "device_prompt",
    totp_secret: str = "",
    job_id: str = "",
    progress_callback: Any = None,
    human_profile: str = "normal",
) -> dict[str, Any]:
    """Perform Google account login on Android via Settings → Accounts.

    All UI interactions use the HumanInteractor module for randomized
    delays, Bezier curve swipes, offset taps, and realistic typing.

    Parameters
    ----------
    device : u2.Device
        Connected uiautomator2 device.
    gmail : str
        Google account email address.
    password : str
        Account password.
    method : str
        2FA method: "device_prompt" or "totp".
    totp_secret : str
        TOTP secret key (required if method is "totp").
    job_id : str
        Job identifier for screenshot naming.
    human_profile : str
        Speed profile for humanized interactions: "slow", "normal", "fast".

    Returns
    -------
    dict with keys:
        status: SUCCESS | WRONG_PASSWORD | UNSAFE | TIMEOUT | ERROR
        message: Human-readable description
        screenshots: list of screenshot paths
    """
    result: dict[str, Any] = {
        "status": "ERROR",
        "message": "",
        "screenshots": [],
    }

    # ── Validate method before doing any work ────────────────────
    valid_methods = {METHOD_DEVICE_PROMPT, METHOD_TOTP}
    if method not in valid_methods:
        result["message"] = (
            f"Unknown 2FA method '{method}'. "
            f"Valid methods are: {', '.join(sorted(valid_methods))}"
        )
        logger.error("[%s] %s", job_id, result["message"])
        return result

    # ── Initialize HumanInteractor ───────────────────────────────
    human = create_human(device, profile=human_profile)
    logger.info("[%s] HumanInteractor initialized (profile=%s)", job_id, human_profile)

    try:
        # ── Step 1: Open Settings → Accounts → Add Account ──────
        # NOTE: Android 11 ReDroid wraps all Settings text labels in
        # invisible Unicode BiDi markers, so exact text= match fails.
        # We use textContains= everywhere for Settings navigation.
        logger.info("[%s] Opening Settings → Add Account", job_id)

        d = device
        found = False

        # Strategy 1: Direct intent to Accounts (most reliable)
        account_intents = [
            "android.settings.SYNC_SETTINGS",
            "android.settings.ADD_ACCOUNT_SETTINGS",
        ]
        for intent in account_intents:
            try:
                await asyncio.to_thread(
                    lambda i=intent: d.shell(f"am start -a {i}")
                )
                await human.sleep(3.0)
                text = await _get_screen_text(device)
                if any(w in text for w in ["account", "add account", "google", "passwords"]):
                    found = True
                    logger.info("[%s] Reached accounts via intent %s", job_id, intent)
                    break
            except Exception:
                continue

        # Strategy 2: Open Settings and find Accounts using textContains
        if not found:
            await launch_app(device, PKG_SETTINGS)
            await human.sleep(2.0)

        account_keywords = ["Accounts", "accounts", "Passwords"]
        if not found:
            for kw in account_keywords:
                tapped = await human.tap_text(kw, timeout=3, contains=True)
                if tapped:
                    found = True
                    logger.info("[%s] Found accounts via humanized tap: %s", job_id, kw)
                    await human.sleep(1.0)
                    break

        # Strategy 3: Scroll Settings using Bezier swipes
        if not found:
            for kw in account_keywords:
                try:
                    scrolled = await human.scroll_to_text(kw, max_scrolls=5, contains=True)
                    if scrolled:
                        tapped = await human.tap_text(kw, timeout=2, contains=True)
                        if tapped:
                            found = True
                            logger.info("[%s] Found accounts after Bezier scroll: %s", job_id, kw)
                            await human.sleep(1.0)
                            break
                except Exception:
                    continue

        if not found:
            result["message"] = "Could not find Accounts in Settings"
            return result

        # Now find and click "Add account"
        await human.sleep(1.0)
        add_tapped = await human.wait_and_tap(
            ["Add account", "add account", "Add an account"],
            timeout=5, contains=True,
        )

        # Select "Google" account type
        await human.think(min_s=0.5, max_s=1.5)
        google_tapped = await human.tap_text("Google", timeout=5, contains=True)

        if not google_tapped:
            result["message"] = "Google account type not found"
            return result

        # Wait for Google login WebView to fully load
        # WebView takes 5-10s to render input fields
        await human.sleep(8.0, jitter=0.2)
        # Small idle behavior while waiting — prevents "frozen screen" detection
        await human.idle_behavior(duration=1.5)
        ss = await take_screenshot(device, "01_login_start", job_id)
        if ss:
            result["screenshots"].append(ss)

        # ── Step 2: Enter email ──────────────────────────────────
        logger.info("[%s] Entering email: %s", job_id, gmail)
        if progress_callback:
            await progress_callback(25, "Submitting Google email")

        # Wait for email input field with retry loop (WebView rendering)
        email_entered = False
        email_deadline = time.time() + 15
        while time.time() < email_deadline and not email_entered:
            # Try resource ID first
            if await asyncio.to_thread(lambda: d(resourceId="identifierId").exists(timeout=2)):
                await human.tap_element(d(resourceId="identifierId"), timeout=2)
                await _type_text_human(device, gmail, human=human)
                email_entered = True
                logger.info("[%s] Email entered via resourceId (humanized)", job_id)
                break

            # Fallback: find EditText
            edit_fields = d(className="android.widget.EditText")
            if await asyncio.to_thread(lambda: edit_fields.exists(timeout=2)):
                await human.tap_element(edit_fields, timeout=2)
                await _type_text_human(device, gmail, human=human)
                email_entered = True
                logger.info("[%s] Email entered via EditText (humanized)", job_id)
                break

            logger.debug("[%s] Email field not found yet, retrying...", job_id)
            await human.sleep(2.0)

        if not email_entered:
            result["message"] = "Could not find email input field (WebView not loaded)"
            return result

        # Click Next button (humanized)
        await human.think(min_s=0.3, max_s=0.8)
        next_tapped = await human.wait_and_tap(
            ["NEXT", "Next", "next"], timeout=3, contains=False,
        )
        if not next_tapped:
            # Fallback: textContains or Enter key
            next_tapped = await human.wait_and_tap(
                ["NEXT", "Next"], timeout=2, contains=True,
            )
            if not next_tapped:
                await human.press_enter()
                logger.info("[%s] Email Next: pressed Enter (humanized)", job_id)

        # Wait for password page to load
        await human.sleep(5.0)
        ss = await take_screenshot(device, "02_email_submitted", job_id)
        if ss:
            result["screenshots"].append(ss)

        # ── Step 3: Enter password ───────────────────────────────
        logger.info("[%s] Waiting for password field...", job_id)

        deadline = time.time() + LOGIN_STEP_TIMEOUT
        while time.time() < deadline:
            state = await _detect_login_state(device)
            if state in ("PASSWORD", "TOTP", "2FA_PROMPT", "SUCCESS", "UNSAFE", "WRONG_PASSWORD"):
                break
            await asyncio.sleep(1)

        if state == "UNSAFE":
            result["status"] = "UNSAFE"
            result["message"] = "Google flagged the login as unsafe"
            return result

        if state == "PASSWORD":
            logger.info("[%s] Entering password (humanized)", job_id)
            if progress_callback:
                await progress_callback(40, "Submitting Google password")

            # Simulate human reading the password prompt
            await human.think(min_s=0.8, max_s=2.0)

            # Find password field
            pw_entered = False
            if await asyncio.to_thread(lambda: d(resourceId="password").exists(timeout=5)):
                await human.tap_element(d(resourceId="password"), timeout=3)
                await _type_text_human(device, password, human=human)
                pw_entered = True
            else:
                # Fallback: find password EditText
                edits = d(className="android.widget.EditText")
                if await asyncio.to_thread(lambda: edits.exists(timeout=5)):
                    await human.tap_element(edits, timeout=3)
                    await _type_text_human(device, password, human=human)
                    pw_entered = True

            if not pw_entered:
                result["message"] = "Could not find password input field"
                return result

            # Click Next (humanized, use textContains for BiDi compat)
            await human.think(min_s=0.3, max_s=0.8)
            pw_next_tapped = await human.wait_and_tap(
                ["Next", "next", "Sign in"], timeout=3, contains=True,
            )
            if not pw_next_tapped:
                await human.press_enter()

            await human.sleep(3.0)
            ss = await take_screenshot(device, "03_password_submitted", job_id)
            if ss:
                result["screenshots"].append(ss)

        # ── Step 4: Handle 2FA ───────────────────────────────────
        logger.info("[%s] Checking for 2FA challenge...", job_id)

        deadline = time.time() + LOGIN_STEP_TIMEOUT
        while time.time() < deadline:
            state = await _detect_login_state(device)
            if state in ("2FA_PROMPT", "TOTP", "SUCCESS", "WRONG_PASSWORD", "TOS"):
                break
            await asyncio.sleep(1)

        if state == "WRONG_PASSWORD":
            result["status"] = "WRONG_PASSWORD"
            result["message"] = "Incorrect password"
            return result

        if state == "2FA_PROMPT":
            if method == "device_prompt":
                logger.info("[%s] Waiting for device prompt approval...", job_id)

                # Try to extract the 2FA prompt details from screen
                prompt_text = await _get_screen_text(device)
                prompt_note = "Google sent a notification to your device. Tap Yes on the notification to verify it's you."
                # Try to extract the number to tap from the screen text
                import re as _re
                number_match = _re.search(r'tap\s+(\d+)', prompt_text)
                device_match = _re.search(r'sent.*?to\s+(?:your\s+)?(.+?)[\.\,]', prompt_text)
                if device_match and number_match:
                    prompt_note = (
                        f"Google sent a notification to your {device_match.group(1).strip()}. "
                        f"Tap Yes on the notification, then tap {number_match.group(1)} on your phone to verify it's you."
                    )
                elif number_match:
                    prompt_note = (
                        f"Google sent a notification to your device. "
                        f"Tap Yes on the notification, then tap {number_match.group(1)} on your phone to verify it's you."
                    )

                if progress_callback:
                    await progress_callback(60, prompt_note)

                ss = await take_screenshot(device, "04_2fa_prompt", job_id)
                if ss:
                    result["screenshots"].append(ss)

                # Wait for user to approve on their phone (up to 120s)
                approval_deadline = time.time() + 120
                while time.time() < approval_deadline:
                    state = await _detect_login_state(device)
                    if state in ("SUCCESS", "TOS"):
                        break
                    await asyncio.sleep(3)

                if state not in ("SUCCESS", "TOS"):
                    result["status"] = "TIMEOUT"
                    result["message"] = "2FA device prompt not approved within 120s"
                    return result

            elif method == "totp":
                # Click "Try another way" to get to TOTP (humanized)
                await human.think(min_s=1.0, max_s=2.5)
                await human.wait_and_tap(
                    ["Try another way", "try another way", "another way"],
                    timeout=5, contains=True,
                )
                await human.sleep(2.0)

                # Select Authenticator option (humanized)
                await human.wait_and_tap(
                    ["Google Authenticator", "Authenticator", "verification code"],
                    timeout=3, contains=True,
                )
                await human.sleep(2.0)

                state = await _detect_login_state(device)

            else:
                # Unknown method — should not reach here due to top-level validation,
                # but guard defensively.
                result["status"] = "ERROR"
                result["message"] = f"Unsupported 2FA method '{method}' at 2FA prompt"
                logger.error("[%s] %s", job_id, result["message"])
                return result

        if state == "TOTP" and totp_secret:
            logger.info("[%s] Entering TOTP code (humanized)", job_id)
            # pyrefly: ignore [missing-import]
            import pyotp
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            logger.info("[%s] TOTP code generated: ***", job_id)

            # Humanized TOTP entry — type digit-by-digit
            await human.think(min_s=0.5, max_s=1.5)
            edits = d(className="android.widget.EditText")
            if await asyncio.to_thread(lambda: edits.exists(timeout=5)):
                await human.tap_element(edits, timeout=3)
                await _type_text_human(device, code, human=human)
                logger.info("[%s] TOTP code typed (humanized)", job_id)

            # Click Next/Verify button (humanized)
            await human.think(min_s=0.3, max_s=0.8)
            btn_tapped = await human.wait_and_tap(
                ["Next", "Verify", "Done", "next", "verify"],
                timeout=3, contains=True,
            )

            # Fallback: press Enter key if no button found
            if not btn_tapped:
                logger.info("[%s] No submit button found, pressing Enter (humanized)", job_id)
                await human.press_enter()

            await human.sleep(5.0)

        # ── Step 4b: Handle security alert ────────────────────────
        # Google may show "Suspicious sign-in" / "Was it you?" alert
        logger.info("[%s] Checking for security alert...", job_id)
        state = await _detect_login_state(device)
        if state == "SECURITY_ALERT":
            logger.info("[%s] Security alert detected, attempting auto-approve", job_id)
            if progress_callback:
                await progress_callback(70, "Security alert detected, auto-approving...")

            from .security_alert import handle_security_alert_flow
            alert_result = await handle_security_alert_flow(
                device=device,
                gmail=gmail,
                job_id=job_id,
            )
            if alert_result["handled"]:
                logger.info("[%s] Security alert handled: %s", job_id, alert_result["message"])
                ss = await take_screenshot(device, "04b_security_alert_approved", job_id)
                if ss:
                    result["screenshots"].append(ss)
            else:
                logger.warning("[%s] Security alert NOT handled: %s", job_id, alert_result["message"])
                result["status"] = "SECURITY_ALERT"
                result["message"] = alert_result["message"]
                return result

        # ── Step 5: Accept Terms of Service ──────────────────────
        deadline = time.time() + 30
        while time.time() < deadline:
            state = await _detect_login_state(device)
            if state in ("SUCCESS", "TOS"):
                break
            await asyncio.sleep(1)

        if state == "TOS":
            logger.info("[%s] Accepting Terms of Service (humanized)", job_id)
            await human.think(min_s=1.0, max_s=3.0)  # "Reading" the TOS
            await human.wait_and_tap(
                ["I agree", "Agree", "Accept"], timeout=3, contains=False,
            )
            await human.sleep(2.0)

            # May need to accept multiple screens (humanized)
            for btn in ["Accept", "More", "Next", "Done", "Skip"]:
                tapped = await human.tap_text(btn, timeout=2, contains=False)
                if tapped:
                    await human.sleep(1.0)

        # ── Step 6: Verify success ───────────────────────────────
        await asyncio.sleep(3)
        ss = await take_screenshot(device, "05_login_result", job_id)
        if ss:
            result["screenshots"].append(ss)

        # Final state check
        state = await _detect_login_state(device)
        if state == "SUCCESS":
            result["status"] = "SUCCESS"
            result["message"] = f"Successfully logged in as {gmail}"
            logger.info("[%s] ✅ Login successful: %s", job_id, gmail)
        else:
            # Check if account actually got added
            from .device import get_logged_in_accounts
            accounts = await get_logged_in_accounts(device)
            if gmail.lower() in [a.lower() for a in accounts]:
                result["status"] = "SUCCESS"
                result["message"] = f"Account {gmail} added (verified via accounts list)"
                logger.info("[%s] ✅ Login verified via accounts: %s", job_id, gmail)
            else:
                result["status"] = "UNKNOWN"
                result["message"] = f"Login state unclear (state={state}), account not found in device"
                logger.warning("[%s] Login outcome uncertain: state=%s", job_id, state)

    except Exception as exc:
        logger.exception("[%s] Login error: %s", job_id, exc)
        result["status"] = "ERROR"
        result["message"] = str(exc)
        ss = await take_screenshot(device, "error_login", job_id)
        if ss:
            result["screenshots"].append(ss)

    return result
