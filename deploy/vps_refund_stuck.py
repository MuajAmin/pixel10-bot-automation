import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append("/root/pixel10-bot-automation")

from bot.database import set_mongo_available
from bot.accounts import refund_job, update_job_status

async def refund_one(telegram_id, job_id):
    print(f"Updating job {job_id} to FAILED...")
    await update_job_status(
        telegram_id,
        job_id,
        "FAILED",
        {"progress": 100, "progress_note": "Job got stuck in PROCESSING due to bot crash/restart", "error": "stuck_processing"}
    )
    
    print(f"Refunding job {job_id}...")
    success = await refund_job(telegram_id, job_id)
    if success:
        print(f"Refund for {job_id} successful!")
    else:
        print(f"Refund for {job_id} failed (already refunded or not found)!")
    print("-" * 40)

async def main():
    set_mongo_available(False)
    
    telegram_id = "6196481482"
    jobs = ["cm4859955d63f149129cbfece0d25ca548"]
    
    for job_id in jobs:
        await refund_one(telegram_id, job_id)

if __name__ == "__main__":
    asyncio.run(main())
