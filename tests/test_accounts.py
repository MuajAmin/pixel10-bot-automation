"""Unit tests for bot.accounts module."""
import pytest
from bot.accounts import (
    default_account,
    normalize_account,
    charge_account,
    balance_credit,
    referral_credit,
    create_job,
    recent_jobs,
)


def test_default_account_shape():
    acc = default_account()
    assert acc["deposit_credit"] == 0
    assert acc["total_deposit"] == 0
    assert isinstance(acc["jobs"], list)
    assert isinstance(acc["credit_ledger"], list)


def test_normalize_fills_missing_keys():
    acc = {"deposit_credit": 5}
    normalize_account(acc)
    assert acc["total_deposit"] == 0
    assert isinstance(acc["jobs"], list)
    assert isinstance(acc["credit_ledger"], list)


def test_balance_credit_deposit_only():
    acc = default_account()
    acc["deposit_credit"] = 10
    assert balance_credit(acc) == 10


def test_charge_insufficient():
    acc = default_account()
    ok, source, dep, ref = charge_account(acc, 1)
    assert not ok
    assert source == ""


def test_charge_success_deposit():
    acc = default_account()
    acc["deposit_credit"] = 5
    ok, source, dep, ref = charge_account(acc, 1)
    assert ok
    assert source == "DEPOSIT"
    assert dep == 1
    assert ref == 0
    assert acc["deposit_credit"] == 4
    assert acc["deposit_spent"] == 1


def test_charge_exact_balance():
    acc = default_account()
    acc["deposit_credit"] = 3
    ok, source, dep, ref = charge_account(acc, 3)
    assert ok
    assert acc["deposit_credit"] == 0
    assert acc["deposit_spent"] == 3


def test_create_job_adds_to_list():
    acc = default_account()
    job = create_job(acc, "test@gmail.com", "pass", "device_prompt", charged=1)
    assert job["gmail"] == "test@gmail.com"
    assert job["status"] == "PENDING"
    assert job["password"] == "[REDACTED]"
    assert len(acc["jobs"]) == 1


def test_recent_jobs_limit():
    acc = default_account()
    for i in range(15):
        create_job(acc, f"test{i}@gmail.com", "pass", "device_prompt")
    assert len(recent_jobs(acc, 10)) == 10
    assert len(recent_jobs(acc, 50)) == 15
