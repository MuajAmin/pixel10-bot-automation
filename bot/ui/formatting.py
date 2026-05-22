"""Formatting helpers: progress bars, status icons, text utilities."""

from __future__ import annotations

from html import escape


RECENT_JOB_LIMIT = 10
PROGRESS_WIDTH = 14
ADMIN_USERS_PAGE_SIZE = 10


def short_text(value: str, limit: int = 18) -> str:
    value = value.strip()
    if limit <= 3:
        return value[:limit]
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def status_emoji(status: str) -> str:
    return {
        "PENDING": "⏳",
        "RUNNING": "🟢",
        "PROCESSING": "⚙️",
        "SUCCESS": "✅",
        "SUCCEEDED": "✅",
        "LOGIN_OK": "🔐",
        "COMPLETED": "🎉",
        "FAILED": "❌",
        "ERROR": "💥",
    }.get(status.upper(), "❔")


def status_label(status: str) -> str:
    return {
        "PENDING": "Queued",
        "RUNNING": "Running",
        "PROCESSING": "Processing",
        "LOGIN_OK": "Login Confirmed",
        "SUCCESS": "Success",
        "SUCCEEDED": "Success",
        "COMPLETED": "Completed",
        "FAILED": "Failed",
        "ERROR": "Error",
    }.get(status.upper(), status.replace("_", " ").title())


def status_badge(status: str) -> str:
    return f"{status_emoji(status)} <b>{escape(status_label(status))}</b>"


def stage_emoji(stage: str) -> str:
    """Return a unique emoji for each job stage."""
    stage_lower = stage.lower()
    if "start" in stage_lower:
        return "🚀"
    if "account" in stage_lower or "check" in stage_lower:
        return "🔍"
    if "credential" in stage_lower or "submit" in stage_lower:
        return "🔑"
    if "verif" in stage_lower or "wait" in stage_lower:
        return "📡"
    if "claim" in stage_lower or "offer" in stage_lower:
        return "🎁"
    if "final" in stage_lower:
        return "🏁"
    if "complet" in stage_lower:
        return "✅"
    if "fail" in stage_lower:
        return "❌"
    return "▪️"


def parse_positive_credit(value: str) -> int | None:
    cleaned = value.strip().replace(",", "")
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].strip()
    if not cleaned.isdigit():
        return None

    try:
        amount = int(cleaned)
    except ValueError:
        return None
    return amount if amount > 0 else None


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def compact_note(note: str, limit: int = 44) -> str:
    return short_text(" ".join(note.split()), limit)


def progress_bar(progress: int) -> str:
    progress = max(0, min(100, progress))
    filled = round((progress / 100) * PROGRESS_WIDTH)
    empty = PROGRESS_WIDTH - filled
    return "█" * filled + "░" * empty


def progress_line(progress: int) -> str:
    return f"{progress_bar(progress)} {progress}%"


def progress_stage(progress: int, status: str) -> str:
    status = status.upper()
    if status == "LOGIN_OK":
        return "Login confirmed"
    if status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}:
        return "Completed"
    if status in {"FAILED", "ERROR"}:
        return "Failed"
    if progress < 10:
        return "Preparing session"
    if progress < 25:
        return "Opening Google"
    if progress < 40:
        return "Checking account"
    if progress < 60:
        return "Submitting password"
    if progress < 75:
        return "Waiting for approval"
    if progress < 90:
        return "Login confirmed"
    if progress < 95:
        return "Checking offer"
    if progress < 100:
        return "Activating offer"
    return "Finishing up"


def progress_flow(progress: int, status: str) -> str:
    normalized = status.upper()
    completed = normalized in {"SUCCESS", "SUCCEEDED", "COMPLETED"}
    failed = normalized in {"FAILED", "ERROR"}
    steps = [
        (0, "Start"),
        (25, "Login"),
        (60, "Verify"),
        (90, "Offer"),
        (100, "Done"),
    ]

    parts = []
    for idx, (start, label) in enumerate(steps):
        next_start = steps[idx + 1][0] if idx + 1 < len(steps) else 101
        if completed or progress >= next_start:
            marker = "●"
        elif not failed and progress >= start:
            marker = "◉"
        else:
            marker = "○"
        parts.append(f"{marker} {label}")
    return " › ".join(parts)
