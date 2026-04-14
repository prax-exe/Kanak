import os
import asyncio
import datetime
from calendar import monthrange
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse

from .commands import handle_message, send_document, send_text
from .database import get_all_users, get_expenses_for_period, get_users_to_notify
from .reports import generate_pdf_report

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
SCHEDULER_SECRET = os.environ.get("SCHEDULER_SECRET", "")
IST = ZoneInfo("Asia/Kolkata")

REMINDER_TEXT = (
    "Hey! Time to log your expenses for today.\n\n"
    "Just type what you spent — e.g. `200 lunch, 50 chai`"
)


async def _notify_scheduler():
    """Runs every minute. Sends reminders to users whose notify_time matches current IST time."""
    while True:
        try:
            now_ist = datetime.datetime.now(IST)
            hhmm = now_ist.strftime("%H:%M")
            users = get_users_to_notify(hhmm)
            for user in users:
                await send_text(user["phone_number"], REMINDER_TEXT)
        except Exception:
            pass
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
    body = await request.json()

    try:
        change_value = body["entry"][0]["changes"][0]["value"]

        # Ignore status updates (delivered, read receipts, etc.)
        if "messages" not in change_value:
            return {"status": "ok"}

        message = change_value["messages"][0]

        # Only handle text messages
        if message.get("type") != "text":
            return {"status": "ok"}

        phone_number = message["from"]
        text = message["text"]["body"]

        background_tasks.add_task(handle_message, phone_number, text)

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

    users = get_all_users()
    for user in users:
        expenses = get_expenses_for_period(user["id"], first_day, last_day)
        if not expenses:
            continue
        pdf_data = generate_pdf_report(expenses, user, first_day)
        month_label = first_day.strftime("%B %Y")
        await send_document(
            user["phone_number"],
            f"kanak_{first_day.strftime('%Y_%m')}.pdf",
            pdf_data,
            f"Your expense report for {month_label} \ud83d\udcca",
            "application/pdf"
        )
