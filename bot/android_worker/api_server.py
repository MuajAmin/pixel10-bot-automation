"""FastAPI server for the Android worker — runs on the VPS alongside ReDroid.

This exposes the Android job runner as an HTTP API so the Telegram bot
(running on Windows) can dispatch jobs to the Linux VPS remotely.

Usage:
    uvicorn bot.android_worker.api_server:app --host 0.0.0.0 --port 8800
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header
from pydantic import BaseModel, model_validator

from .config import API_KEY, API_PORT
from .device import connect_device, device_health_check, get_device_properties
from .runner import run_android_job

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

_BASE32_SECRET_RE = re.compile(r"^[A-Z2-7]{32}$")


def _normalize_totp_secret(secret: str) -> str:
    normalized = re.sub(r"\s+", "", secret or "").replace("=", "").upper()
    if normalized and not _BASE32_SECRET_RE.fullmatch(normalized):
        raise ValueError("totp_secret must be a 32-character base32 token")
    return normalized


def _split_account_token(token: str) -> tuple[str, str, str] | None:
    if "---" not in token:
        return None
    parts = token.strip().split("---")
    if len(parts) != 3:
        raise ValueError("credential token must use email---password---2fa_secret")
    return parts[0].strip().lower(), parts[1], _normalize_totp_secret(parts[2])

app = FastAPI(
    title="Gemini Pixel Offer Claim Bot Android Worker",
    description="ReDroid-based Android worker API for Pixel offer claiming",
    version="1.0.0",
)

# ── Persistent job store ──────────────────────────────────────────

DATA_DIR = Path("/app/data")
JOBS_FILE = DATA_DIR / "jobs.json"


def _load_persisted_jobs() -> dict[str, dict[str, Any]]:
    """Load jobs from persistent JSON file."""
    if not JOBS_FILE.exists():
        return {}
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.error("Failed to load persisted jobs: %s", exc)
    return {}


def _save_persisted_jobs(jobs: dict[str, dict[str, Any]]) -> None:
    """Save jobs to persistent JSON file, keeping the most recent 100 jobs."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Keep only the most recent 100 jobs by created_at time to prevent file bloat
        sorted_jobs = sorted(jobs.items(), key=lambda item: item[1].get("created_at", 0), reverse=True)
        to_save = dict(sorted_jobs[:100])
        
        temp_file = JOBS_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2, ensure_ascii=False)
        temp_file.replace(JOBS_FILE)
    except Exception as exc:
        logger.error("Failed to save persisted jobs: %s", exc)


_jobs: dict[str, dict[str, Any]] = {}
_job_tasks: dict[str, asyncio.Task] = {}

# Load jobs from disk and clean up stuck ones
try:
    _jobs = _load_persisted_jobs()
    _restarts_fixed = 0
    for jid, jdata in _jobs.items():
        if jdata.get("status") == "PROCESSING":
            jdata["status"] = "ERROR"
            jdata["message"] = "Worker API restarted during job execution"
            _restarts_fixed += 1
    if _restarts_fixed > 0:
        logger.info("Marked %d stale 'PROCESSING' jobs as 'ERROR' on startup", _restarts_fixed)
        _save_persisted_jobs(_jobs)
except Exception as e:
    logger.error("Failed to initialize jobs on startup: %s", e)
    _jobs = {}


# ── Auth ─────────────────────────────────────────────────────────


async def verify_api_key(x_api_key: str = Header(default="")) -> str:
    """API key authentication — always enforced."""
    if not API_KEY or API_KEY == "changeme":
        logger.warning("API_KEY is unset or still 'changeme' — rejecting request")
        raise HTTPException(status_code=500, detail="Server API key not configured")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


def _gc_old_jobs() -> None:
    """Remove completed jobs older than 1 hour from in-memory store."""
    cutoff = time.time() - 3600
    stale = [
        jid for jid, jdata in _jobs.items()
        if jdata.get("status") != "PROCESSING" and jdata.get("created_at", 0) < cutoff
    ]
    if stale:
        for jid in stale:
            _jobs.pop(jid, None)
            _job_tasks.pop(jid, None)
        _save_persisted_jobs(_jobs)


# ── Models ───────────────────────────────────────────────────────


class JobRequest(BaseModel):
    gmail: str
    password: str
    method: str = "device_prompt"
    totp_secret: str = ""
    job_id: str = ""

    @model_validator(mode="after")
    def normalize_credentials(self) -> "JobRequest":
        parsed = _split_account_token(self.gmail)
        if parsed is None and "---" in self.password:
            parsed = _split_account_token(self.password)
        if parsed is not None:
            self.gmail, self.password, parsed_secret = parsed
            self.totp_secret = self.totp_secret or parsed_secret
            self.method = "totp"

        if self.method.startswith("2FA Secret:"):
            self.totp_secret = self.totp_secret or self.method.split(":", 1)[1].strip()
            self.method = "totp"

        self.gmail = self.gmail.strip().lower()
        self.totp_secret = _normalize_totp_secret(self.totp_secret)
        if self.totp_secret:
            self.method = "totp"
        return self


class JobResponse(BaseModel):
    job_id: str
    status: str
    message: str = ""


class JobResult(BaseModel):
    job_id: str
    status: str
    offer_url: str = ""
    offer_type: str = ""
    message: str = ""
    progress: int = 0
    progress_note: str = ""
    screenshots: list[str] = []
    device_info: dict[str, Any] = {}
    elapsed_seconds: float = 0


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Root endpoint returning API status and details."""
    return {
        "name": "Gemini Pixel Offer Claim Bot Android Worker API",
        "status": "online",
        "version": "1.0.0"
    }


@app.get("/healthz")
async def healthz():
    """Unauthenticated liveness probe for Docker/Kubernetes healthchecks."""
    return {"status": "ok"}


@app.get("/health")
async def health(api_key: str = Depends(verify_api_key)):
    """Check if the Android worker and ReDroid container are healthy."""
    try:
        device = await connect_device(timeout=15)
        health = await device_health_check(device)
        props = await get_device_properties(device)
        return {
            "status": "healthy",
            "device": health,
            "properties": props,
            "active_jobs": len([j for j in _jobs.values() if j.get("status") == "PROCESSING"]),
        }
    except Exception as exc:
        return {
            "status": "unhealthy",
            "error": str(exc),
        }


@app.post("/jobs", response_model=JobResponse)
async def create_job(
    request: JobRequest,
    api_key: str = Depends(verify_api_key),
):
    """Create and start a new login + offer claim job."""
    _gc_old_jobs()
    job_id = request.job_id or str(uuid.uuid4())[:12]

    # Check for duplicate
    if job_id in _jobs and _jobs[job_id].get("status") == "PROCESSING":
        raise HTTPException(status_code=409, detail=f"Job {job_id} already running")

    # Check for concurrent Gmail
    for jid, jdata in _jobs.items():
        if (
            jdata.get("gmail", "").lower() == request.gmail.lower()
            and jdata.get("status") == "PROCESSING"
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Job for {request.gmail} already running (job {jid})",
            )

    # Initialize job state
    _jobs[job_id] = {
        "job_id": job_id,
        "gmail": request.gmail,
        "status": "PROCESSING",
        "progress": 0,
        "progress_note": "Starting...",
        "offer_url": "",
        "offer_type": "",
        "message": "",
        "screenshots": [],
        "device_info": {},
        "elapsed_seconds": 0,
        "created_at": time.time(),
    }
    _save_persisted_jobs(_jobs)

    # Progress callback to update in-memory state
    async def on_progress(percent: int, note: str) -> None:
        if job_id in _jobs:
            _jobs[job_id]["progress"] = percent
            _jobs[job_id]["progress_note"] = note
            _save_persisted_jobs(_jobs)

    # Start job in background
    task = asyncio.create_task(
        _run_job_wrapper(job_id, request, on_progress)
    )
    _job_tasks[job_id] = task

    logger.info("Job %s created for %s", job_id, request.gmail)
    return JobResponse(job_id=job_id, status="PROCESSING", message="Job started")


@app.get("/jobs/{job_id}", response_model=JobResult)
async def get_job(
    job_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Get the current status and result of a job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    jdata = _jobs[job_id]
    return JobResult(
        job_id=job_id,
        status=jdata.get("status", "UNKNOWN"),
        offer_url=jdata.get("offer_url", ""),
        offer_type=jdata.get("offer_type", ""),
        message=jdata.get("message", ""),
        progress=jdata.get("progress", 0),
        progress_note=jdata.get("progress_note", ""),
        screenshots=jdata.get("screenshots", []),
        device_info=jdata.get("device_info", {}),
        elapsed_seconds=jdata.get("elapsed_seconds", 0),
    )


@app.delete("/jobs/{job_id}")
async def cancel_job(
    job_id: str,
    api_key: str = Depends(verify_api_key),
):
    """Cancel a running job."""
    if job_id not in _job_tasks:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    task = _job_tasks[job_id]
    if not task.done():
        task.cancel()
        if job_id in _jobs:
            _jobs[job_id]["status"] = "CANCELLED"
            _save_persisted_jobs(_jobs)
        return {"message": f"Job {job_id} cancelled"}

    return {"message": f"Job {job_id} already completed"}


@app.get("/jobs")
async def list_jobs(api_key: str = Depends(verify_api_key)):
    """List all jobs (active and recent)."""
    return {
        "jobs": [
            {
                "job_id": jid,
                "gmail": jdata.get("gmail", ""),
                "status": jdata.get("status", ""),
                "progress": jdata.get("progress", 0),
                "elapsed_seconds": jdata.get("elapsed_seconds", 0),
            }
            for jid, jdata in _jobs.items()
        ],
        "total": len(_jobs),
        "active": len([j for j in _jobs.values() if j.get("status") == "PROCESSING"]),
    }


# ── Internal ─────────────────────────────────────────────────────


async def _run_job_wrapper(
    job_id: str,
    request: JobRequest,
    progress_callback: Any,
) -> None:
    """Wrapper that runs the job and updates the in-memory store."""
    try:
        result = await run_android_job(
            gmail=request.gmail,
            password=request.password,
            method=request.method,
            totp_secret=request.totp_secret,
            job_id=job_id,
            progress_callback=progress_callback,
        )

        if job_id in _jobs:
            _jobs[job_id].update({
                "status": result.get("status", "ERROR"),
                "offer_url": result.get("offer_url", ""),
                "offer_type": result.get("offer_type", ""),
                "message": result.get("message", ""),
                "screenshots": result.get("screenshots", []),
                "device_info": result.get("device_info", {}),
                "elapsed_seconds": result.get("elapsed_seconds", 0),
            })
            _save_persisted_jobs(_jobs)

    except asyncio.CancelledError:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "CANCELLED"
            _save_persisted_jobs(_jobs)
    except Exception as exc:
        logger.exception("Job %s crashed: %s", job_id, exc)
        if job_id in _jobs:
            _jobs[job_id]["status"] = "ERROR"
            _jobs[job_id]["message"] = str(exc)
            _save_persisted_jobs(_jobs)


# ── Startup ──────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "bot.android_worker.api_server:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
    )
