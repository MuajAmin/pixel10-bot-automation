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

    try:
        # ── Verify automation script exists ──────────────────────
        if not _AUTOMATION_SCRIPT.exists():
            result["message"] = f"core/automation.py not found at {_AUTOMATION_SCRIPT}"
            logger.error("[%s] %s", job_id, result["message"])
            return result

        # ── Build command ────────────────────────────────────────
        adb_target = f"{ADB_HOST}:{ADB_PORT}"
        cmd = [
            "python3",
            str(_AUTOMATION_SCRIPT),
            "--gmail", gmail,
            "--password", password,
            "--job-id", job_id,
            "--adb-target", adb_target,
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

    except Exception as exc:
        logger.exception("[%s] Job error: %s", job_id, exc)
        result["status"] = "ERROR"
        result["message"] = str(exc)

    finally:
        result["elapsed_seconds"] = result.get("elapsed_seconds") or round(
            time.time() - start_time, 1
        )

        if progress_callback:
            await progress_callback(100, result.get("message", "Done"))

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
