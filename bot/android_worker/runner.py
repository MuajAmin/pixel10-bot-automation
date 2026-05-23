"""Job orchestrator for the Android worker.

Bridges the Telegram bot's HTTP API to the core/automation.py pipeline.

INTEGRATION FLOW:
    Telegram bot (Windows)
         │
    HTTP POST /jobs {gmail, password, job_id}
         │
    api_server.py → run_android_job()  (this file)
         │
    subprocess: python3 core/automation.py --gmail X --password Y --job-id Z
         │
    core/automation.py calls: bash core/build_props.sh {base,swap,restore}
         │
    uiautomator2 → ReDroid container (localhost:5555)

The subprocess approach ensures:
  - core/automation.py runs as a standalone process (clean state)
  - build_props.sh calls work correctly (subprocess from subprocess)
  - Stdout markers ([STATUS], [CLAIM_URL]) are parsed for results
  - Progress updates are relayed to the Telegram bot via callback
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from .config import ADB_HOST, ADB_PORT, TOTAL_JOB_TIMEOUT

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────
# This file lives at: bot/android_worker/runner.py
# Project root is 2 levels up.
_THIS_DIR = Path(__file__).resolve().parent               # bot/android_worker/
_PROJECT_DIR = _THIS_DIR.parent.parent                     # project root
_AUTOMATION_SCRIPT = _PROJECT_DIR / "core" / "automation.py"
_RESET_PACKAGES = (
    "com.google.android.gsf",
    "com.google.android.gms",
    "com.android.vending",
    "com.google.android.apps.subscriptions.red",
)
_RUNTIME_COMPAT_ERRORS = (
    "NoSuchMethodError",
    "ClassNotFoundException",
)


async def _adb_shell(
    adb_target: str,
    command: str,
    *,
    tolerate_compat: bool = True,
    timeout: float = 60.0,
) -> str:
    """Run an ADB shell command and return combined output.

    Some Android 13 system services emit Java API compatibility exceptions when
    SDK 36 props are forced. Those are logged but treated as non-fatal for
    cleanup/setup commands.
    """
    proc = await asyncio.create_subprocess_exec(
        "adb",
        "-s",
        adb_target,
        "shell",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_PROJECT_DIR),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        # Kill the process. Under uvloop the process may already have exited
        # before we get here, so both ProcessLookupError and the more general
        # OSError must be tolerated.
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        # Guard proc.wait() with its own timeout.
        # Critical: asyncio.wait_for(proc.communicate()) internally parks
        # pipe-readers in uvloop's event loop.  When wait_for cancels them
        # the transport is left in an inconsistent state where a bare
        # `await proc.wait()` blocks forever, which prevents the
        # TimeoutError from ever being raised and leaves result["message"]
        # as "" in the caller, producing a false "Done" report.
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass  # give up; the OS will reap the zombie
        raise TimeoutError(
            f"adb shell timed out after {timeout:.1f}s for {command!r}"
        ) from exc
    output = "\n".join(
        part.decode("utf-8", errors="replace").strip()
        for part in (stdout, stderr)
        if part
    ).strip()
    if proc.returncode != 0:
        if tolerate_compat and any(marker in output for marker in _RUNTIME_COMPAT_ERRORS):
            logger.warning("ADB compatibility exception tolerated for %r: %s", command, output)
            return output
        raise RuntimeError(f"adb shell failed ({proc.returncode}) for {command!r}: {output}")
    return output


def _safe_profile_name(job_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id or str(int(time.time()))).strip("._-")
    return f"pixel10_job_{suffix[:48] or int(time.time())}"


def _parse_android_users(output: str) -> dict[int, str]:
    users: dict[int, str] = {}
    for match in re.finditer(r"UserInfo\{(\d+):([^:}]+)", output or ""):
        users[int(match.group(1))] = match.group(2)
    return users


async def _list_android_users(adb_target: str) -> dict[int, str]:
    output = await _adb_shell(adb_target, "pm list users", tolerate_compat=True)
    return _parse_android_users(output)


async def _wait_user_present(adb_target: str, user_id: int, *, present: bool, timeout_sec: float = 30.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        users = await _list_android_users(adb_target)
        if (user_id in users) is present:
            return True
        await asyncio.sleep(1.0)
    return False


async def _wait_current_user(adb_target: str, user_id: int, timeout_sec: float = 30.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            output = await _adb_shell(adb_target, "am get-current-user", tolerate_compat=True, timeout=10)
            if output.strip().splitlines()[-1].strip() == str(user_id):
                return True
        except Exception:
            pass
        await asyncio.sleep(1.0)
    return False


async def _create_isolated_user(adb_target: str, job_id: str) -> int:
    """Create and switch into a per-job Android multi-user profile."""
    profile_name = _safe_profile_name(job_id)
    output = await _adb_shell(adb_target, f"pm create-user {profile_name}")
    match = re.search(r"(?:id|user)\s+(\d+)", output, flags=re.IGNORECASE)
    if not match:
        raise RuntimeError(f"Could not parse created Android user id from: {output}")

    user_id = int(match.group(1))
    if user_id <= 0:
        raise RuntimeError(f"Refusing to use invalid isolated Android user id: {user_id}")

    if not await _wait_user_present(adb_target, user_id, present=True, timeout_sec=30):
        raise RuntimeError(f"Created Android user {user_id} was not visible in pm list users")

    await _adb_shell(adb_target, f"am start-user {user_id}", tolerate_compat=True)
    await _adb_shell(adb_target, f"am switch-user {user_id}", tolerate_compat=True)
    if not await _wait_current_user(adb_target, user_id, timeout_sec=30):
        raise RuntimeError(f"Android did not switch to isolated user {user_id}")
    logger.info("[%s] Created isolated Android user profile: %s", job_id, user_id)
    return user_id


async def _reset_profile_packages(adb_target: str, user_id: int, job_id: str) -> None:
    """Force-stop and clear target app namespaces inside the isolated profile."""
    for package in _RESET_PACKAGES:
        for command in (
            f"am force-stop --user {user_id} {package}",
            f"pm clear --user {user_id} {package}",
        ):
            try:
                await _adb_shell(adb_target, command, tolerate_compat=True)
            except Exception as exc:
                logger.warning("[%s] Profile package reset failed for %s: %s", job_id, command, exc)


async def _remove_isolated_user(adb_target: str, user_id: int, job_id: str) -> None:
    """Remove the per-job Android user profile and its data."""
    if user_id <= 0:
        logger.warning("[%s] Refusing to remove owner/invalid Android user id: %s", job_id, user_id)
        return

    await _reset_profile_packages(adb_target, user_id, job_id)

    try:
        await _adb_shell(adb_target, "am switch-user 0", tolerate_compat=True)
    except Exception as exc:
        logger.warning("[%s] Could not switch back to owner user before teardown: %s", job_id, exc)

    try:
        await _adb_shell(adb_target, f"am stop-user -w {user_id}", tolerate_compat=True, timeout=45)
    except Exception as exc:
        logger.warning("[%s] Could not stop isolated Android user profile %s: %s", job_id, user_id, exc)

    try:
        await _adb_shell(adb_target, f"pm remove-user {user_id}", tolerate_compat=True)
    except Exception as exc:
        logger.warning("[%s] Could not remove isolated Android user profile %s: %s", job_id, user_id, exc)
        return

    if await _wait_user_present(adb_target, user_id, present=False, timeout_sec=45):
        logger.info("[%s] Removed isolated Android user profile: %s", job_id, user_id)
    else:
        logger.warning("[%s] Android user profile %s still listed after removal request", job_id, user_id)


async def run_android_job(
    gmail: str,
    password: str,
    method: str = "device_prompt",
    totp_secret: str = "",
    job_id: str = "",
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Execute a complete Android-based login + offer claim job.

    This function spawns core/automation.py as a subprocess and parses
    its stdout for status markers and the final JSON result.

    Parameters
    ----------
    gmail : str
        Google account email.
    password : str
        Account password.
    method : str
        2FA method: "device_prompt" or "totp" (forwarded to automation).
    totp_secret : str
        TOTP secret (if method is "totp").
    job_id : str
        Unique job identifier for logging/screenshots.
    progress_callback : callable, optional
        async function(percent: int, note: str) for progress updates.

    Returns
    -------
    dict with keys:
        status: CLAIMED | OFFER_FOUND | NO_OFFER | LOGIN_FAILED | ERROR | BLOCKED
        offer_url: Redeem URL (if found)
        offer_type: "pixel_specific" | "gemini" | ""
        message: Human-readable status message
        screenshots: list of screenshot file paths
        device_info: dict of device properties
        elapsed_seconds: float
    """
    start_time = time.time()
    result: dict[str, Any] = {
        "status": "ERROR",
        "offer_url": "",
        "offer_type": "",
        "message": "",
        "screenshots": [],
        "device_info": {},
        "elapsed_seconds": 0,
    }
    profile_user_id: int | None = None
    process: asyncio.subprocess.Process | None = None

    try:
        # ── Verify automation script exists ──────────────────────
        if not _AUTOMATION_SCRIPT.exists():
            result["message"] = f"core/automation.py not found at {_AUTOMATION_SCRIPT}"
            logger.error("[%s] %s", job_id, result["message"])
            return result

        # ── Build command ────────────────────────────────────────
        adb_target = f"{ADB_HOST}:{ADB_PORT}"
        profile_user_id = await _create_isolated_user(adb_target, job_id or str(int(time.time())))
        await _reset_profile_packages(adb_target, profile_user_id, job_id)

        if method.startswith("2FA Secret:") and not totp_secret:
            totp_secret = method.split(":", 1)[1].strip()
            method = "totp"
        cmd = [
            "python3",
            str(_AUTOMATION_SCRIPT),
            "--gmail", gmail,
            "--password", password,
            "--totp-secret", totp_secret,
            "--job-id", job_id,
            "--adb-target", adb_target,
            "--android-user", str(profile_user_id),
        ]

        logger.info("[%s] ═══ Job started for %s ═══", job_id, gmail)
        logger.info("[%s] Running: python3 core/automation.py --gmail %s --job-id %s", job_id, gmail, job_id)

        if progress_callback:
            await progress_callback(5, "Starting automation pipeline")

        # ── Spawn subprocess ─────────────────────────────────────
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_PROJECT_DIR),
        )

        # ── Stream stdout line-by-line (real-time progress) ──────
        stdout_lines: list[str] = []
        json_block: list[str] = []
        in_json = False
        claim_url = ""
        last_status = ""

        # Progress mapping: status markers → progress percentage
        progress_map = {
            "LOGIN_SUCCESS": (60, "Google login successful"),
            "LOGIN_FAILED": (100, "Google login failed"),
            "2FA_TRIGGERED": (35, "2FA verification required"),
            "APP_CONSTRAINT": (100, "Application constraint detected"),
            "GOOGLE_ONE_NOT_INSTALLED": (70, "Google One not installed"),
            "OFFER_BLOCKED_BY_GOOGLE": (100, "Offer blocked by Google"),
            "CLAIMED": (100, "Offer claimed successfully!"),
            "OFFER_FOUND": (95, "Offer found!"),
            "NO_OFFER": (100, "No offer available"),
            "COMPLETED": (100, "Job completed"),
            "ERROR": (100, "Error occurred"),
        }

        assert process.stdout is not None
        while True:
            line_bytes = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=TOTAL_JOB_TIMEOUT,
            )
            if not line_bytes:
                break

            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            stdout_lines.append(line)

            # Log all output for debugging
            logger.info("[%s] [stdout] %s", job_id, line)

            # ── Parse [STATUS]: markers ──────────────────────────
            status_match = re.match(r'\[STATUS\]:\s*(.+)', line)
            if status_match:
                last_status = status_match.group(1).strip()
                logger.info("[%s] Status marker: %s", job_id, last_status)

                if progress_callback and last_status in progress_map:
                    pct, note = progress_map[last_status]
                    await progress_callback(pct, note)

            # ── Parse [CLAIM_URL]: markers ───────────────────────
            url_match = re.match(r'\[CLAIM_URL\]:\s*(.+)', line)
            if url_match:
                claim_url = url_match.group(1).strip()
                logger.info("[%s] Claim URL found: %s", job_id, claim_url)

            verify_match = re.match(r'\[VERIFY_NUMBER\]:\s*(\d{2})', line)
            if verify_match and progress_callback:
                code = verify_match.group(1)
                await progress_callback(45, f"Tap {code} on your phone to verify sign-in")

            # ── Detect JSON block at end of output ───────────────
            if line.strip() == "{":
                in_json = True
                json_block = [line]
            elif in_json:
                json_block.append(line)
                if line.strip() == "}":
                    in_json = False

        # ── Wait for process to exit ─────────────────────────────
        stderr_bytes = await process.stderr.read() if process.stderr else b""
        await process.wait()
        returncode = process.returncode

        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        if stderr_text:
            for err_line in stderr_text.split("\n"):
                logger.warning("[%s] [stderr] %s", job_id, err_line)

        logger.info("[%s] Process exited with code %d", job_id, returncode)

        # ── Parse JSON result from stdout ────────────────────────
        parsed_json = None
        if json_block:
            try:
                parsed_json = json.loads("\n".join(json_block))
                logger.info("[%s] Parsed JSON result: %s", job_id, parsed_json.get("status"))
            except json.JSONDecodeError:
                logger.warning("[%s] Failed to parse JSON block from stdout", job_id)

        # ── Build final result ───────────────────────────────────
        if parsed_json:
            result["status"] = parsed_json.get("status", "ERROR")
            result["offer_url"] = parsed_json.get("url", "") or claim_url
            result["message"] = parsed_json.get("message", "")
            result["elapsed_seconds"] = parsed_json.get("elapsed_seconds", 0)
        elif last_status:
            # Fallback: use status markers if JSON parsing failed
            result["status"] = _translate_status(last_status)
            result["offer_url"] = claim_url
            result["message"] = last_status
        else:
            result["status"] = "ERROR"
            result["message"] = f"No status output (exit code {returncode})"

        # Always prefer explicit claim URL
        if claim_url:
            result["offer_url"] = claim_url

        # ── Collect screenshots ──────────────────────────────────
        screenshots_dir = _PROJECT_DIR / "screenshots"
        if screenshots_dir.exists():
            for f in screenshots_dir.glob(f"job_{job_id}_*.png"):
                result["screenshots"].append(str(f))

    except asyncio.TimeoutError:
        result["status"] = "TIMEOUT"
        result["message"] = f"Automation timed out after {TOTAL_JOB_TIMEOUT}s"
        logger.warning("[%s] Job timed out", job_id)

        # Kill the subprocess if it's still running
        try:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
        except Exception:
            pass

    except asyncio.CancelledError:
        # CancelledError is a BaseException (not Exception) so the broad
        # `except Exception` below will NOT catch it.  Without this handler
        # result["message"] stays "" and the progress_callback fires with an
        # empty string, which downstream code silently renders as "Done".
        result["status"] = "TIMEOUT"
        result["message"] = "Job cancelled — ADB subprocess did not respond in time"
        logger.warning("[%s] Job cancelled (CancelledError)", job_id)
        if process and process.returncode is None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
        raise  # CancelledError must always be re-raised

    except Exception as exc:
        logger.exception("[%s] Job error: %s", job_id, exc)
        result["status"] = "ERROR"
        result["message"] = str(exc)

    finally:
        if profile_user_id is not None:
            await _remove_isolated_user(f"{ADB_HOST}:{ADB_PORT}", profile_user_id, job_id)

        result["elapsed_seconds"] = result.get("elapsed_seconds") or round(
            time.time() - start_time, 1
        )

        if progress_callback:
            # Use `or` so that an empty-string message also falls back to
            # "Done".  dict.get(key, default) only fires when the key is
            # absent; since "message" is always initialised to "", the
            # original `result.get("message", "Done")` always returned "".
            await progress_callback(100, result.get("message") or "Done")

        logger.info(
            "[%s] ═══ Job finished: %s (%.1fs) ═══",
            job_id, result["status"], result["elapsed_seconds"],
        )

    return result


def _translate_status(marker: str) -> str:
    """Map stdout status markers to API-level status codes."""
    mapping = {
        "LOGIN_SUCCESS": "SUCCESS",
        "LOGIN_FAILED": "LOGIN_FAILED",
        "2FA_TRIGGERED": "2FA_REQUIRED",
        "GOOGLE_ONE_NOT_INSTALLED": "ERROR",
        "OFFER_BLOCKED_BY_GOOGLE": "NO_OFFER",
        "CLAIMED": "CLAIMED",
        "OFFER_FOUND": "OFFER_FOUND",
        "NO_OFFER": "NO_OFFER",
        "COMPLETED": "NO_OFFER",
        "ERROR": "ERROR",
    }
    return mapping.get(marker, "ERROR")
