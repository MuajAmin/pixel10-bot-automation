"""Telegram command & callback handlers.

Split into sub-modules for maintainability:
- common.py  — shared utilities (edit_message, validators, state mgmt)
- user.py    — user-facing commands & menus (/start, profile, balance, jobs)
- admin.py   — admin panel (/admin, user management, credit, broadcast)
- verify.py  — verify job creation FSM (gmail → password → method → dispatch)
"""

from bot.handlers.admin import cmd_admin
from bot.handlers.user import cmd_start, handle_menu, handle_text

__all__ = ["cmd_start", "cmd_admin", "handle_menu", "handle_text"]
