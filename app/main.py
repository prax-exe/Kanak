import os
import hmac
import json
import hashlib
import asyncio
import logging
import datetime
from calendar import monthrange
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

from .commands import handle_message, handle_voice_message, send_document, send_text
from .database import get_all_users, get_expenses_for_period, get_users_to_notify
from .reports import generate_pdf_report

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
SCHEDULER_SECRET = os.environ.get("SCHEDULER_SECRET", "")
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")


def _verify_signature(body: bytes, signature_header: str) -> bool:
    """Validate X-Hub-Signature-256 from Meta to reject spoofed webhook calls."""
    if not APP_SECRET:
        return True  # not configured — skip (warn in logs)
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])
IST = ZoneInfo("Asia/Kolkata")

REMINDER_TEXT = (
    "Hey! Time to log your expenses for today.\n\n"
    "Just type what you spent — e.g. `200 lunch, 50 chai`"
)


async def _notify_scheduler():
    """Runs every minute. Sends reminders to users whose notify_time matches their local time."""
    while True:
        try:
            users = get_users_to_notify()
            for user in users:
                tz_name = user.get("notify_timezone") or "Asia/Kolkata"
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = IST
                now_local = datetime.datetime.now(tz)
                if now_local.strftime("%H:%M") == user["notify_time"]:
                    await send_text(user["phone_number"], REMINDER_TEXT)
        except Exception:
            logger.exception("Notification scheduler error")
        # Sleep until the start of the next minute
        await asyncio.sleep(60 - datetime.datetime.now(IST).second)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_notify_scheduler())
    yield
    task.cancel()


app = FastAPI(title="Kanak", description="WhatsApp Expense Tracker", lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "running", "app": "Kanak \u2014 WhatsApp Expense Tracker"}


# --- WhatsApp webhook verification (GET) ---
@app.get("/webhook")
def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Webhook verification failed")


# --- Incoming WhatsApp messages (POST) ---
@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body_bytes, sig):
        raise HTTPException(status_code=403, detail="Invalid signature")

    body = json.loads(body_bytes)

    try:
        change_value = body["entry"][0]["changes"][0]["value"]

        # Ignore status updates (delivered, read receipts, etc.)
        if "messages" not in change_value:
            return {"status": "ok"}

        message = change_value["messages"][0]

        phone_number = message["from"]
        msg_type = message.get("type")

        if msg_type == "text":
            text = message["text"]["body"]
            background_tasks.add_task(handle_message, phone_number, text)
        elif msg_type == "audio":
            media_id = message["audio"]["id"]
            background_tasks.add_task(handle_voice_message, phone_number, media_id)

    except (KeyError, IndexError):
        # Malformed payload — ignore silently
        pass

    # Always return 200 to WhatsApp to prevent retries
    return {"status": "ok"}


# --- Monthly report trigger (called by GitHub Actions cron) ---
@app.post("/trigger/monthly-report")
async def trigger_monthly_report(request: Request, background_tasks: BackgroundTasks):
    secret = request.headers.get("X-Scheduler-Secret", "")
    if not SCHEDULER_SECRET or secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    background_tasks.add_task(_send_monthly_reports)
    return {"status": "triggered", "message": "Monthly reports queued"}


async def _send_monthly_reports():
    today = datetime.date.today()
    # Report covers the previous month
    first_day = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    last_day = first_day.replace(day=monthrange(first_day.year, first_day.month)[1])
    month_label = first_day.strftime("%B %Y")

    users = get_all_users()
    logger.info("Monthly report run started: %s, %d users", month_label, len(users))
    sent = 0
    for user in users:
        try:
            expenses = get_expenses_for_period(user["id"], first_day, last_day)
            if not expenses:
                continue
            pdf_data = generate_pdf_report(expenses, user, first_day)
            await send_document(
                user["phone_number"],
                f"kanak_{first_day.strftime('%Y_%m')}.pdf",
                pdf_data,
                f"Your expense report for {month_label} \ud83d\udcca",
                "application/pdf"
            )
            sent += 1
        except Exception:
            logger.exception("Monthly report failed for user %s", user.get("id"))
    logger.info("Monthly report run complete: %d/%d sent", sent, len(users))
