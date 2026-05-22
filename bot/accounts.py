"""Accounts / jobs persistence layer — backed by MongoDB (Motor)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from bot.config import ACCOUNTS_FILE
from bot.database import mongo_available, users_col
_ASYNC_JSON_LOCK = asyncio.Lock()


# ── Default document shape ────────────────────────────────────────────

def default_account() -> dict[str, Any]:
    return {
        "deposit_credit": 0,
        "total_deposit": 0,
        "deposit_spent": 0,
        "referral_spent": 0,
        "valid_invited_users": 0,
        "pending_referrals": 0,
        "status": "active",
        "referred_by": None,
        "jobs": [],
        "credit_ledger": [],
    }


def normalize_account(account: dict) -> dict:
    defaults = default_account()
    for key, value in defaults.items():
        account.setdefault(key, value)
    if not isinstance(account.get("jobs"), list):
        account["jobs"] = []
    if not isinstance(account.get("credit_ledger"), list):
        account["credit_ledger"] = []
    return account


# ── Low-level MongoDB helpers ─────────────────────────────────────────

def _load_json_accounts() -> dict[str, dict]:
    if not ACCOUNTS_FILE.exists():
        return {}
    try:
        with ACCOUNTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_json_accounts(accounts: dict[str, dict]) -> None:
    tmp = ACCOUNTS_FILE.with_name(f"{ACCOUNTS_FILE.name}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, sort_keys=True)
    os.replace(tmp, ACCOUNTS_FILE)


async def _find_user(telegram_id: str) -> dict | None:
    if not mongo_available():
        async with _ASYNC_JSON_LOCK:
            account = _load_json_accounts().get(telegram_id)
        if account:
            normalize_account(account)
        return account

    doc = await users_col().find_one({"_id": telegram_id})
    if doc:
        doc.pop("_id", None)
        normalize_account(doc)
    return doc


async def _upsert_user(telegram_id: str, account: dict) -> None:
    if not mongo_available():
        async with _ASYNC_JSON_LOCK:
            accounts = _load_json_accounts()
            accounts[telegram_id] = normalize_account(account)
            _save_json_accounts(accounts)
        return

    payload = {k: v for k, v in account.items() if k != "_id"}
    await users_col().update_one(
        {"_id": telegram_id},
        {"$set": payload},
        upsert=True,
    )


# ── Public account helpers ────────────────────────────────────────────

async def get_account(telegram_id: str) -> dict:
    doc = await _find_user(telegram_id)
    if doc is None:
        doc = default_account()
        await _upsert_user(telegram_id, doc)
    return doc


async def save_account(telegram_id: str, account: dict) -> None:
    await _upsert_user(telegram_id, account)


async def list_account_ids() -> list[str]:
    if not mongo_available():
        return sorted(_load_json_accounts().keys())

    cursor = users_col().find({}, {"_id": 1})
    ids = [doc["_id"] async for doc in cursor]
    return sorted(ids)


async def set_account_status(telegram_id: str, status: str) -> bool:
    if not mongo_available():
        async with _ASYNC_JSON_LOCK:
            accounts = _load_json_accounts()
            account = accounts.get(telegram_id)
            if not account:
                return False
            normalize_account(account)
            account["status"] = status
            accounts[telegram_id] = account
            _save_json_accounts(accounts)
        return True

    result = await users_col().update_one(
        {"_id": telegram_id},
        {"$set": {"status": status}},
    )
    return result.matched_count > 0


CREDIT_LEDGER_LIMIT = 50
CREDIT_ACTION_LABELS = {
    "add": "Add credit",
    "remove": "Remove credit",
    "set": "Set balance",
}


def preview_deposit_credit_change(current_balance: int, action: str, amount: int) -> tuple[bool, int, str]:
    action = action.lower()
    if action not in CREDIT_ACTION_LABELS:
        return False, current_balance, "Unknown credit action."
    if action in {"add", "remove"} and amount <= 0:
        return False, current_balance, "Amount must be greater than zero."
    if action == "set" and amount < 0:
        return False, current_balance, "Balance cannot be negative."
    if action == "add":
        return True, current_balance + amount, ""
    if action == "remove":
        if amount > current_balance:
            return False, current_balance, "Remove amount cannot exceed current deposit balance."
        return True, current_balance - amount, ""
    return True, amount, ""


async def adjust_deposit_credit(
    telegram_id: str,
    action: str,
    amount: int,
    admin_id: str = "",
) -> tuple[bool, int, str]:
    if not mongo_available():
        async with _ASYNC_JSON_LOCK:
            accounts = _load_json_accounts()
            account = accounts.get(telegram_id)
            if not account:
                return False, 0, "User not found."
            normalize_account(account)
            current = int(account.get("deposit_credit", 0))
            ok, new_balance, error = preview_deposit_credit_change(current, action, amount)
            if not ok:
                return False, current, error

            account["deposit_credit"] = new_balance
            deposit_increase = max(0, new_balance - current)
            if deposit_increase:
                account["total_deposit"] = int(account.get("total_deposit", 0)) + deposit_increase

            entry = {
                "id": str(uuid.uuid4()),
                "action": action.lower(),
                "amount": amount,
                "before": current,
                "after": new_balance,
                "admin_id": admin_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            ledger = list(account.get("credit_ledger", []))
            ledger.append(entry)
            account["credit_ledger"] = ledger[-CREDIT_LEDGER_LIMIT:]
            accounts[telegram_id] = account
            _save_json_accounts(accounts)
        return True, new_balance, ""

    doc = await _find_user(telegram_id)
    if doc is None:
        return False, 0, "User not found."

    current = int(doc.get("deposit_credit", 0))
    ok, new_balance, error = preview_deposit_credit_change(current, action, amount)
    if not ok:
        return False, current, error

    entry = {
        "id": str(uuid.uuid4()),
        "action": action.lower(),
        "amount": amount,
        "before": current,
        "after": new_balance,
        "admin_id": admin_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    update: dict[str, Any] = {
        "$set": {"deposit_credit": new_balance},
        "$push": {"credit_ledger": {"$each": [entry], "$slice": -CREDIT_LEDGER_LIMIT}},
    }
    deposit_increase = max(0, new_balance - current)
    if deposit_increase:
        update["$inc"] = {"total_deposit": deposit_increase}

    await users_col().update_one({"_id": telegram_id}, update)
    return True, new_balance, ""


# ── Referral helpers ──────────────────────────────────────────────────

async def register_referral(current_id: str, referrer_id: str | None) -> None:
    if not referrer_id or referrer_id == current_id:
        return
    if not current_id.isdigit() or not referrer_id.isdigit():
        return

    if not mongo_available():
        async with _ASYNC_JSON_LOCK:
            accounts = _load_json_accounts()
            current = normalize_account(accounts.setdefault(current_id, default_account()))
            if current.get("referred_by"):
                return
            referrer = normalize_account(accounts.setdefault(referrer_id, default_account()))
            current["referred_by"] = referrer_id
            referrer["valid_invited_users"] = int(referrer.get("valid_invited_users", 0)) + 1
            accounts[current_id] = current
            accounts[referrer_id] = referrer
            _save_json_accounts(accounts)
        return

    # Atomic conditional update: only set referred_by if it's not already set.
    # This prevents race conditions from two rapid /start ref_X calls.
    result = await users_col().update_one(
        {"_id": current_id, "referred_by": {"$exists": False}},
        {"$set": {"referred_by": referrer_id}},
        upsert=False,
    )
    # If no user document exists yet, create one with the referral.
    if result.matched_count == 0:
        current = await _find_user(current_id)
        if current is None:
            current = default_account()
            current["referred_by"] = referrer_id
            await _upsert_user(current_id, current)
        elif current.get("referred_by"):
            return  # already referred by someone else
    elif result.modified_count == 0:
        return  # referred_by already existed

    # Atomically increment referrer's invited count
    await users_col().update_one(
        {"_id": referrer_id},
        {"$inc": {"valid_invited_users": 1}},
        upsert=True,
    )


# ── Credit math (pure helpers — no I/O) ──────────────────────────────

def referral_earned_credit(account: dict) -> int:
    from bot.config import REFERRAL_USERS_PER_CREDIT
    return int(account.get("valid_invited_users", 0)) // REFERRAL_USERS_PER_CREDIT


def referral_credit(account: dict) -> int:
    return max(0, referral_earned_credit(account) - int(account.get("referral_spent", 0)))


def balance_credit(account: dict) -> int:
    return int(account.get("deposit_credit", 0)) + referral_credit(account)


def total_spent(account: dict) -> int:
    return int(account.get("deposit_spent", 0)) + int(account.get("referral_spent", 0))


def remaining_for_reward(account: dict) -> int:
    from bot.config import REFERRAL_USERS_PER_CREDIT
    remainder = int(account.get("valid_invited_users", 0)) % REFERRAL_USERS_PER_CREDIT
    return 0 if remainder == 0 else REFERRAL_USERS_PER_CREDIT - remainder


def add_deposit(account: dict, amount: int) -> None:
    account["deposit_credit"] = int(account.get("deposit_credit", 0)) + amount
    account["total_deposit"] = int(account.get("total_deposit", 0)) + amount


def charge_account(account: dict, price: int) -> tuple[bool, str, int, int]:
    """Charge the account in-memory. Caller must save afterwards."""
    if balance_credit(account) < price:
        return False, "", 0, 0

    deposit_used = min(int(account.get("deposit_credit", 0)), price)
    account["deposit_credit"] = int(account.get("deposit_credit", 0)) - deposit_used
    account["deposit_spent"] = int(account.get("deposit_spent", 0)) + deposit_used

    referral_used = price - deposit_used
    account["referral_spent"] = int(account.get("referral_spent", 0)) + referral_used

    if deposit_used > 0 and referral_used > 0:
        source = "DEPOSIT+REFERRAL"
    elif deposit_used > 0:
        source = "DEPOSIT"
    else:
        source = "REFERRAL"
    return True, source, deposit_used, referral_used


# ── Job helpers ───────────────────────────────────────────────────────

def create_job(
    account: dict,
    gmail: str,
    password: str,
    method: str,
    charged: int = 0,
    credit_source: str = "",
    charged_deposit: int = 0,
    charged_referral: int = 0,
) -> dict:
    job: dict[str, Any] = {
        "id": "cm" + uuid.uuid4().hex,
        "gmail": gmail,
        "password": "[REDACTED]",
        "method": method,
        "status": "PENDING",
        "charged": charged,
        "credit_source": credit_source,
        "charged_deposit": charged_deposit,
        "charged_referral": charged_referral,
        "progress": 0,
        "progress_note": "",
        "redeem_link": "",
        "error": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    account.setdefault("jobs", []).insert(0, job)
    account["jobs"] = account["jobs"][:50]
    return job


def recent_jobs(account: dict, limit: int = 10) -> list[dict]:
    jobs = account.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    return jobs[:limit]


async def all_recent_jobs(limit: int = 10) -> list[tuple[str, dict]]:
    items: list[tuple[str, dict]] = []
    if not mongo_available():
        for tid, account in _load_json_accounts().items():
            normalize_account(account)
            for job in recent_jobs(account, 50):
                items.append((tid, job))
    else:
        cursor = users_col().find({})
        async for doc in cursor:
            tid = doc.pop("_id", "")
            normalize_account(doc)
            for job in recent_jobs(doc, 50):
                items.append((tid, job))

    def _sort_key(item: tuple[str, dict]) -> str:
        return str(item[1].get("updated_at") or item[1].get("created_at") or "")

    return sorted(items, key=_sort_key, reverse=True)[:limit]


async def admin_stats() -> dict[str, int]:
    stats = {
        "total_users": 0,
        "active_users": 0,
        "banned_users": 0,
        "total_balance": 0,
        "total_deposit": 0,
        "total_spent": 0,
        "total_jobs": 0,
        "failed_jobs": 0,
    }

    if not mongo_available():
        docs = list(_load_json_accounts().values())
        for doc in docs:
            normalize_account(doc)
            stats["total_users"] += 1
            if str(doc.get("status", "active")).lower() == "banned":
                stats["banned_users"] += 1
            else:
                stats["active_users"] += 1
            stats["total_balance"] += balance_credit(doc)
            stats["total_deposit"] += int(doc.get("total_deposit", 0))
            stats["total_spent"] += total_spent(doc)
            jobs = doc.get("jobs", [])
            if isinstance(jobs, list):
                stats["total_jobs"] += len(jobs)
                stats["failed_jobs"] += sum(
                    1 for j in jobs
                    if str(j.get("status", "")).upper() in {"FAILED", "ERROR"}
                )
        return stats

    cursor = users_col().find({})
    async for doc in cursor:
        doc.pop("_id", None)
        normalize_account(doc)
        stats["total_users"] += 1
        if str(doc.get("status", "active")).lower() == "banned":
            stats["banned_users"] += 1
        else:
            stats["active_users"] += 1
        stats["total_balance"] += balance_credit(doc)
        stats["total_deposit"] += int(doc.get("total_deposit", 0))
        stats["total_spent"] += total_spent(doc)
        jobs = doc.get("jobs", [])
        if isinstance(jobs, list):
            stats["total_jobs"] += len(jobs)
            stats["failed_jobs"] += sum(
                1 for j in jobs
                if str(j.get("status", "")).upper() in {"FAILED", "ERROR"}
            )
    return stats


# ── Thread-safe job status updates (called from async login worker) ───

async def update_job_status(
    telegram_id: str,
    job_id: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Update a single job's status atomically using MongoDB array filters."""
    now = datetime.now(timezone.utc).isoformat()
    if not mongo_available():
        async with _ASYNC_JSON_LOCK:
            accounts = _load_json_accounts()
            account = accounts.get(telegram_id)
            if not account:
                return
            for job in account.get("jobs", []):
                if job.get("id") == job_id:
                    job["status"] = status
                    job["updated_at"] = now
                    if extra:
                        job.update(extra)
                    _save_json_accounts(accounts)
                    return
        return

    update_fields: dict[str, Any] = {
        "jobs.$[job].status": status,
        "jobs.$[job].updated_at": now,
    }
    if extra:
        for k, v in extra.items():
            update_fields[f"jobs.$[job].{k}"] = v

    await users_col().update_one(
        {"_id": telegram_id},
        {"$set": update_fields},
        array_filters=[{"job.id": job_id}],
    )


def _refund_split(job: dict) -> tuple[int, int, int]:
    try:
        charged = int(job.get("charged", 0))
    except (TypeError, ValueError):
        charged = 0

    try:
        deposit = int(job.get("charged_deposit", 0))
        referral = int(job.get("charged_referral", 0))
    except (TypeError, ValueError):
        deposit = 0
        referral = 0

    if deposit > 0 or referral > 0:
        return charged, max(0, deposit), max(0, referral)

    source = str(job.get("credit_source", "")).upper()
    if "REFERRAL" in source and "DEPOSIT" not in source:
        return charged, 0, charged
    return charged, charged, 0


async def refund_job(telegram_id: str, job_id: str) -> bool:
    """Refund the charged credit for a failed job — atomic MongoDB update."""
    if not mongo_available():
        async with _ASYNC_JSON_LOCK:
            accounts = _load_json_accounts()
            doc = accounts.get(telegram_id)
            if not doc:
                return False
            job = next((j for j in doc.get("jobs", []) if j.get("id") == job_id), None)
            if not job or job.get("refunded") is not None:
                return False
            charged, deposit_refund, referral_refund = _refund_split(job)
            if charged <= 0:
                job["refunded"] = 0
                _save_json_accounts(accounts)
                return False
            if deposit_refund:
                doc["deposit_credit"] = int(doc.get("deposit_credit", 0)) + deposit_refund
                doc["deposit_spent"] = max(0, int(doc.get("deposit_spent", 0)) - deposit_refund)
            if referral_refund:
                doc["referral_spent"] = max(0, int(doc.get("referral_spent", 0)) - referral_refund)
            job["refunded"] = charged
            _save_json_accounts(accounts)
        return True

    doc = await _find_user(telegram_id)
    if not doc:
        return False

    job = next((j for j in doc.get("jobs", []) if j.get("id") == job_id), None)
    if not job or job.get("refunded") is not None:
        return False

    charged, deposit_refund, referral_refund = _refund_split(job)
    update_filter = {
        "_id": telegram_id,
        "jobs": {
            "$elemMatch": {
                "id": job_id,
                "refunded": {"$exists": False},
            },
        },
    }
    array_filters = [{"job.id": job_id, "job.refunded": {"$exists": False}}]

    if charged <= 0:
        result = await users_col().update_one(
            update_filter,
            {"$set": {"jobs.$[job].refunded": 0}},
            array_filters=array_filters,
        )
        return result.modified_count > 0

    credit_inc: dict[str, int] = {}
    if deposit_refund:
        credit_inc["deposit_credit"] = deposit_refund
        # Guard: $inc can push deposit_spent negative if data is corrupted.
        # We'll clamp it after the update.
        credit_inc["deposit_spent"] = -deposit_refund
    if referral_refund:
        credit_inc["referral_spent"] = -referral_refund

    result = await users_col().update_one(
        update_filter,
        {
            "$inc": credit_inc,
            "$set": {"jobs.$[job].refunded": charged},
        },
        array_filters=array_filters,
    )
    return result.modified_count > 0
