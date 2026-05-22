"""Tests for bot.config module."""
import os

def test_config_loads():
    """Verify config module loads without errors."""
    from bot.config import BOT_TOKEN, ACCOUNTS_FILE, SCREENSHOTS_DIR
    assert isinstance(BOT_TOKEN, str)
    assert ACCOUNTS_FILE.name == "accounts.json"
    assert SCREENSHOTS_DIR.name == "screenshots"
