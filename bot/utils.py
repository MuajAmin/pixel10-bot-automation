"""Shared utility helpers used across the bot."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update


def user_identity(update: "Update") -> tuple[str, str]:
    """Extract (telegram_id, username) from an Update."""
    user = update.effective_user
    telegram_id = str(user.id) if user else os.getenv("TELEGRAM_ID", "0")
    username = (
        user.username
        if user and user.username
        else (
            user.first_name
            if user and user.first_name
            else os.getenv("TELEGRAM_USERNAME", os.getenv("MOCK_USERNAME", "user"))
        )
    )
    return telegram_id, username


def clean_email(email: str) -> str:
    """Strip whitespace from an email address."""
    return email.strip()


def int_env(name: str, default: int, minimum: int | None = None) -> int:
    """Read an integer from the environment with optional minimum clamp."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value
