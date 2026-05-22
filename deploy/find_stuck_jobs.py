import json
from pathlib import Path
from datetime import datetime

ACCOUNTS_FILE = Path("/root/pixel10-bot-automation/accounts.json")

def main():
    if not ACCOUNTS_FILE.exists():
        print("No accounts.json found.")
        return
    
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        accounts = json.load(f)
        
    print("=== Stuck / Active Jobs in accounts.json ===")
    found = False
    for user_id, data in accounts.items():
        jobs = data.get("jobs", [])
        for job in jobs:
            status = job.get("status", "")
            if status in ("PENDING", "PROCESSING", "RUNNING"):
                found = True
                print(f"User: {user_id}")
                print(f"  Job ID: {job.get('id')}")
                print(f"  Email: {job.get('gmail')}")
                print(f"  Status: {status}")
                print(f"  Created At: {job.get('created_at')}")
                print(f"  Updated At: {job.get('updated_at')}")
                print(f"  Refunded: {job.get('refunded')}")
                print(f"  Progress Note: {job.get('progress_note')}")
                print("-" * 40)
                
    if not found:
        print("No stuck or active jobs found.")

if __name__ == "__main__":
    main()
