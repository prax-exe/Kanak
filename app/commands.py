import os
import time
import asyncio
import httpx
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)
from calendar import monthrange
from collections import defaultdict
from .database import (
    get_or_create_user, log_expenses, get_last_expense,
    update_expense, delete_expense, get_expenses_for_period,
    set_default_currency, clear_all_expenses,
    set_notify_time, clear_notify_time
)
from .parser import parse_expenses
from .reports import generate_pdf_report, generate_csv_report, format_amount

PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WA_API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
WA_MEDIA_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"

# Shared HTTP client — reuses TLS connections across all requests
_http_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=10.0)
    return _http_client

# Exchange rate cache: {"USD": (rate, timestamp), "EUR": (rate, timestamp)}
_rate_cache: dict[str, tuple[float, float]] = {}
_RATE_TTL = 1800  # 30 minutes

# In-memory session state — acceptable for single-instance deployment
# state values: "idle" | "awaiting_edit"
user_sessions: dict[str, dict] = {}

HELP_TEXT = """*Kanak \u2014 WhatsApp Expense Tracker*

*Log expenses \u2014 just type naturally:*
\u2022 `coffee 50` \u2192 Rs.50 under Food
\u2022 `4000 bike repair` \u2192 Rs.4000 under Transport
\u2022 `199 netflix, 800 groceries` \u2192 two expenses at once
\u2022 `$15 spotify` \u2192 USD expense
\u2022 `\u20ac10 museum ticket` \u2192 EUR expense (INR equivalent saved automatically)
\u2022 `4k rent` \u2192 Rs.4000 (k = thousands)

*Currency prefixes you can use:*
\u2022 `\u20b9` / `rs` / `inr` / `rupees` \u2192 Indian Rupee
\u2022 `$` / `usd` / `dollars` \u2192 US Dollar
\u2022 `\u20ac` / `eur` / `euros` \u2192 Euro

*View expenses:*
\u2022 `today` \u2014 today's expenses
\u2022 `week` \u2014 this week summary
\u2022 `month` \u2014 this month summary

*Reports:*
\u2022 `report` \u2014 PDF for this month
\u2022 `report csv` \u2014 CSV for this month
\u2022 `report last month` \u2014 previous month PDF

*Edit & delete:*
\u2022 `edit last` \u2014 edit your last entry
\u2022 `delete last` / `undo` \u2014 delete last entry

*Settings:*
\u2022 `currency INR` \u2014 set default to Rupees
\u2022 `currency USD` \u2014 set default to Dollars
\u2022 `currency EUR` \u2014 set default to Euros
\u2022 `notify 9pm` \u2014 daily reminder at 9 PM IST
\u2022 `notify off` \u2014 turn off reminder

\u2022 `help` \u2014 show this menu"""


async def _get_inr_rate(currency: str) -> float | None:
    """Return cached INR rate for currency, fetching if stale."""
    cached = _rate_cache.get(currency)
    if cached and (time.monotonic() - cached[1]) < _RATE_TTL:
        return cached[0]
    try:
        resp = await get_http_client().get(
            f"https://api.frankfurter.dev/v1/latest?from={currency}&to=INR",
            timeout=5.0
        )
        if resp.status_code == 200:
            rate = float(resp.json()["rates"]["INR"])
            _rate_cache[currency] = (rate, time.monotonic())
            return rate
    except Exception:
        pass
    return None


async def fetch_inr_equivalent(amount: float, currency: str) -> float | None:
    if currency == "INR":
        return None
    rate = await _get_inr_rate(currency)
    if rate is None:
        return None
    return round(amount * rate, 2)


async def send_text(to: str, message: str):
    client = get_http_client()
    resp = await client.post(
        WA_API_URL,
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message, "preview_url": False}
        },
    )
    if resp.status_code != 200:
        logger.error("WhatsApp API error %s: %s", resp.status_code, resp.text)


async def send_document(to: str, filename: str, data: bytes, caption: str, mime_type: str):
    client = get_http_client()
    upload = await client.post(
        WA_MEDIA_URL,
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        files={"file": (filename, data, mime_type)},
        data={"messaging_product": "whatsapp"},
        timeout=30.0
    )
    upload.raise_for_status()
    media_id = upload.json()["id"]

    await client.post(
        WA_API_URL,
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": {
                "id": media_id,
                "caption": caption,
                "filename": filename
            }
        },
    )


def _format_expense_list(expenses: list[dict]) -> str:
    if not expenses:
        return "No expenses found."
    lines = []
    for e in expenses:
        d = date.fromisoformat(e["expense_date"])
        lines.append(
            f"\u2022 {d.strftime('%d %b')}  {format_amount(e['amount'], e['currency'])}  \u2014  {e['description']}  _{e['category']}_"
        )
    return "\n".join(lines)


def _format_summary(expenses: list[dict], label: str) -> str:
    if not expenses:
        return f"No expenses for {label}."

    by_category: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for e in expenses:
        by_category[e["category"]][e["currency"]] += e["amount"]

    totals: dict[str, float] = defaultdict(float)
    for e in expenses:
        totals[e["currency"]] += e["amount"]

    lines = [f"*{label}*\n"]
    for cat, currencies in sorted(by_category.items()):
        parts = [format_amount(amt, cur) for cur, amt in currencies.items()]
        lines.append(f"\u2022 {cat}: {' + '.join(parts)}")

    total_str = " + ".join(format_amount(amt, cur) for cur, amt in totals.items())
    lines.append(f"\n*Total: {total_str}*")
    lines.append(f"_{len(expenses)} transaction{'s' if len(expenses) != 1 else ''}_")
    return "\n".join(lines)


def _parse_time_input(raw: str) -> str | None:
    """Parse user time input into HH:MM (24h) string, or None if unparseable."""
    import re
    raw = raw.strip().lower().replace(".", ":").replace(" ", "")
    # Formats: 9pm, 9:30pm, 21:00, 9am, 8:30am
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?(am|pm)?$', raw)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    meridiem = m.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


async def handle_message(phone_number: str, message_text: str):
    text = message_text.strip()
    text_lower = text.lower()

    user = get_or_create_user(phone_number)
    session = user_sessions.get(phone_number, {"state": "idle"})

    # --- Awaiting edit confirmation ---
    if session.get("state") == "awaiting_edit":
        expense_id = session.get("expense_id")
        if expense_id:
            parsed = parse_expenses(text, user["default_currency"])
            if parsed:
                e = parsed[0]
                update_expense(expense_id, {
                    "amount": e.amount,
                    "currency": e.currency,
                    "description": e.description,
                    "category": e.category
                })
                user_sessions.pop(phone_number, None)
                await send_text(phone_number,
                    f"\u2713 Updated: {format_amount(e.amount, e.currency)} \u2014 {e.description} ({e.category})")
            else:
                await send_text(phone_number, "Couldn't parse that. Try: `3500 bike repair`\n\nSend `cancel` to abort.")
                if text_lower == "cancel":
                    user_sessions.pop(phone_number, None)
                    await send_text(phone_number, "Edit cancelled.")
        return

    # --- Currency setting ---
    if text_lower.startswith("currency "):
        cur = text_lower.split(" ", 1)[1].strip().upper()
        if cur in ("INR", "USD", "EUR"):
            set_default_currency(phone_number, cur)
            await send_text(phone_number, f"Default currency set to *{cur}*")
        else:
            await send_text(phone_number, "Supported: `currency INR`, `currency USD`, or `currency EUR`")
        return

    # --- Notify command ---
    if text_lower.startswith("notify"):
        arg = text_lower[len("notify"):].strip()
        if arg == "off":
            clear_notify_time(phone_number)
            await send_text(phone_number, "Daily reminder turned off.")
        else:
            hhmm = _parse_time_input(arg)
            if hhmm:
                set_notify_time(phone_number, hhmm)
                await send_text(
                    phone_number,
                    f"Done! I'll remind you to log your expenses every day at *{hhmm} IST*.\n\nType `notify off` to cancel."
                )
            else:
                await send_text(
                    phone_number,
                    "Couldn't read that time.\n\nTry:\n• `notify 9pm`\n• `notify 21:00`\n• `notify 8:30am`\n• `notify off` to disable"
                )
        return

    # --- Help / Greetings ---
    greetings = {"help", "hi", "hello", "/help", "/start", "hey", "helo",
                 "vanakkam", "namaste", "namaskar", "yo", "sup", "start", "menu"}
    if text_lower in greetings:
        await send_text(phone_number, HELP_TEXT)
        return

    # --- Delete last ---
    if text_lower in ("delete last", "undo"):
        last = get_last_expense(user["id"])
        if last:
            delete_expense(last["id"])
            await send_text(phone_number,
                f"Deleted: {format_amount(last['amount'], last['currency'])} \u2014 {last['description']}")
        else:
            await send_text(phone_number, "No recent expense to delete.")
        return

    # --- Clear all expenses ---
    if text_lower == "clear":
        clear_all_expenses(user["id"])
        await send_text(phone_number, "All your expense data has been wiped.")
        return

    # --- Edit last ---
    if text_lower == "edit last":
        last = get_last_expense(user["id"])
        if last:
            user_sessions[phone_number] = {"state": "awaiting_edit", "expense_id": last["id"]}
            await send_text(phone_number,
                f"Last entry: *{format_amount(last['amount'], last['currency'])}* \u2014 {last['description']} ({last['category']})\n\n"
                f"Send the corrected entry (or `cancel`):")
        else:
            await send_text(phone_number, "No recent expense to edit.")
        return

    # --- Date queries ---
    today = date.today()

    if text_lower == "today":
        expenses = get_expenses_for_period(user["id"], today, today)
        total = sum(e["amount"] for e in expenses if e["currency"] == user["default_currency"])
        reply = _format_expense_list(expenses)
        if expenses:
            reply += f"\n\n*{len(expenses)} expense{'s' if len(expenses)!=1 else ''} today*"
        await send_text(phone_number, reply)
        return

    if text_lower in ("week", "this week"):
        start = today - timedelta(days=today.weekday())
        expenses = get_expenses_for_period(user["id"], start, today)
        await send_text(phone_number, _format_summary(expenses, "This Week"))
        return

    if text_lower in ("month", "this month", "summary"):
        start = today.replace(day=1)
        expenses = get_expenses_for_period(user["id"], start, today)
        await send_text(phone_number, _format_summary(expenses, today.strftime("%B %Y")))
        return

    # --- Reports ---
    if text_lower.startswith("report"):
        is_last_month = "last month" in text_lower
        if is_last_month:
            first_day = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        else:
            first_day = today.replace(day=1)

        last_day = first_day.replace(day=monthrange(first_day.year, first_day.month)[1])
        expenses = get_expenses_for_period(user["id"], first_day, last_day)
        month_label = first_day.strftime("%B %Y")

        if "csv" in text_lower:
            csv_data = generate_csv_report(expenses)
            await send_document(
                phone_number,
                f"kanak_{first_day.strftime('%Y_%m')}.csv",
                csv_data,
                f"Expenses \u2014 {month_label}",
                "text/csv"
            )
        else:
            pdf_data = generate_pdf_report(expenses, user, first_day)
            await send_document(
                phone_number,
                f"kanak_{first_day.strftime('%Y_%m')}.pdf",
                pdf_data,
                f"Expenses \u2014 {month_label}",
                "application/pdf"
            )
        return

    # --- Default: parse as expense ---
    is_first = get_last_expense(user["id"]) is None
    parsed = parse_expenses(text, user["default_currency"])

    if not parsed:
        await send_text(phone_number,
            "Couldn't read that as an expense.\n\n"
            "Try: `4000 bike repair` or `199 netflix, 800 groceries`\n\n"
            "Type *help* to see all commands.")
        return

    inr_equivs = await asyncio.gather(
        *[fetch_inr_equivalent(e.amount, e.currency) for e in parsed]
    )
    for expense, equiv in zip(parsed, inr_equivs):
        expense.inr_equivalent = equiv

    log_expenses(user["id"], parsed, text)

    # Build confirmation
    lines = [f"\u2713 {format_amount(e.amount, e.currency)} \u2014 {e.description} _{e.category}_" for e in parsed]
    reply = "\n".join(lines)

    if len(parsed) > 1:
        totals: dict[str, float] = defaultdict(float)
        for e in parsed:
            totals[e.currency] += e.amount
        total_str = " + ".join(format_amount(amt, cur) for cur, amt in totals.items())
        reply += f"\n\n*{len(parsed)} expenses logged \u2014 {total_str}*"

    if is_first:
        reply += "\n\n_Welcome to Kanak! Type *help* to see all commands._"

    await send_text(phone_number, reply)
