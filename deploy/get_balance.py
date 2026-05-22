import json
from pathlib import Path

ACCOUNTS_FILE = Path("/root/pixel10-bot-automation/accounts.json")

def main():
    if not ACCOUNTS_FILE.exists():
        return
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        accounts = json.load(f)
    user = accounts.get("6196481482", {})
    print(f"User 6196481482 Balance:")
    print(f"  Deposit Credit: {user.get('deposit_credit')}")
    print(f"  Deposit Spent: {user.get('deposit_spent')}")
    print(f"  Referral Spent: {user.get('referral_spent')}")
    print(f"  Valid Invited Users: {user.get('valid_invited_users')}")
    print(f"  Pending Referrals: {user.get('pending_referrals')}")
    print(f"  Jobs count: {len(user.get('jobs', []))}")
    print(f"  Status: {user.get('status')}")

if __name__ == "__main__":
    main()
