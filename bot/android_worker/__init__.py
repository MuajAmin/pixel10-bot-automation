"""Android worker package — ReDroid-based offer claiming.

Note: uiautomator2-dependent modules (humanize, device, google_login, offer_claim)
are only available inside the worker-api Docker container. The Telegram bot
on the VPS host only uses client.py (HTTP) and runner.py (subprocess).
"""

from __future__ import annotations

# Lazy imports: don't import uiautomator2-dependent modules at package level.
# They're only needed inside the Docker container, not by the Telegram bot.
__all__: list[str] = ["HumanInteractor", "create_human"]


def __getattr__(name: str):
    """Lazy import for uiautomator2-dependent classes."""
    if name in ("HumanInteractor", "create_human"):
        from .humanize import HumanInteractor, create_human
        return {"HumanInteractor": HumanInteractor, "create_human": create_human}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

