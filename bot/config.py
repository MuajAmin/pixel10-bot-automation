"""Bot-level configuration loaded from environment / api.env."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_config_logger = logging.getLogger(__name__)

# Load env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / "api.env")

# ── Telegram ─────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_admin_ids_raw = os.getenv("ADMIN_USER_IDS", "").strip()
ADMIN_USER_IDS = {item.strip() for item in _admin_ids_raw.split(",") if item.strip()}
if not ADMIN_USER_IDS:
    _config_logger.warning(
        "ADMIN_USER_IDS is empty — no Telegram user will have admin access. "
        "Set the ADMIN_USER_IDS environment variable to a comma-separated list of Telegram IDs."
    )

# ── UI ───────────────────────────────────────────────────────────────
BOT_TITLE = os.getenv("BOT_TITLE", "BDGeminBot")
BOT_USERNAME = os.getenv("BOT_USERNAME", "BDGeminBot")
DEFAULT_NAME = os.getenv("USER_NAME")

# ── Economy ──────────────────────────────────────────────────────────
REFERRAL_USERS_PER_CREDIT = int(os.getenv("REFERRAL_USERS_PER_CREDIT", "10"))
VERIFY_PRICE = int(os.getenv("VERIFY_PRICE", "1"))

# ── Paths ────────────────────────────────────────────────────────────
ACCOUNTS_FILE = _PROJECT_ROOT / "accounts.json"
SCREENSHOTS_DIR = _PROJECT_ROOT / "screenshots"

# Ensure directories exist
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# int_env is available from bot.utils for callers that need the minimum clamp.
