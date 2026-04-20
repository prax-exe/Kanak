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
    get_or_create_user, log_expenses, get_last_expense, get_last_batch_expenses,
    update_expense, delete_expense, get_expenses_for_period,
    set_default_currency, clear_all_expenses,
    set_notify_time, set_user_timezone, clear_notify_time, delete_user,
    set_budget, clear_budget,
    set_user_session, clear_user_session,
    search_expenses,
)
from .parser import parse_expenses
from .reports import generate_pdf_report, generate_excel_report, format_amount

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

# Per-phone rate limiting: track message timestamps in a sliding window
_rate_limits: dict[str, list[float]] = {}
_RATE_WINDOW = 60   # seconds
_RATE_MAX = 20      # messages per window

def _check_rate_limit(phone_number: str) -> bool:
    """Return True if this phone number has exceeded the rate limit."""
    now = time.monotonic()
    timestamps = [t for t in _rate_limits.get(phone_number, []) if now - t < _RATE_WINDOW]
    _rate_limits[phone_number] = timestamps
    if len(timestamps) >= _RATE_MAX:
        return True
    _rate_limits[phone_number].append(now)
    return False

# Common timezone aliases → IANA names
TIMEZONE_ALIASES: dict[str, str] = {
    "IST":  "Asia/Kolkata",
    "EST":  "America/New_York",
    "EDT":  "America/New_York",
    "CST":  "America/Chicago",
    "CDT":  "America/Chicago",
    "MST":  "America/Denver",
    "MDT":  "America/Denver",
    "PST":  "America/Los_Angeles",
    "PDT":  "America/Los_Angeles",
    "GMT":  "UTC",
    "UTC":  "UTC",
    "BST":  "Europe/London",
    "CET":  "Europe/Paris",
    "CEST": "Europe/Paris",
    "SGT":  "Asia/Singapore",
    "JST":  "Asia/Tokyo",
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
    "WAT":  "Africa/Lagos",
    "EAT":  "Africa/Nairobi",
    "GST":  "Asia/Dubai",
    "PKT":  "Asia/Karachi",
    "BDT":  "Asia/Dhaka",
    "NPT":  "Asia/Kathmandu",
    "LKT":  "Asia/Colombo",
}
_TZ_REVERSE = {v: k for k, v in TIMEZONE_ALIASES.items()}

HELP_TEXT = """*Kanak \u2014 WhatsApp Expense Tracker*

*Log expenses \u2014 just type naturally:*
\u2022 `coffee 50` \u2192 Rs.50 under Food
\u2022 `4000 bike repair` \u2192 Rs.4000 under Transport
\u2022 `199 netflix, 800 groceries` \u2192 two expenses at once
\u2022 `$15 spotify` \u2192 USD expense
\u2022 `\u20ac10 museum ticket` \u2192 EUR expense (INR equivalent saved automatically)
\u2022 `4k rent` \u2192 Rs.4000 (k = thousands)

*Or send a voice note:*
\u2022 Just record and send \u2014 speak your expenses naturally
\u2022 _"spent two hundred on lunch and fifty on chai"_
\u2022 _"four thousand rent, eight hundred electricity"_
Kanak will transcribe and log them automatically.

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
\u2022 `report excel` \u2014 Excel (.xlsx) for this month
\u2022 `report last month` \u2014 previous month PDF

*Search:*
\u2022 `search petrol` \u2014 find expenses matching a keyword
\u2022 `search netflix` \u2014 shows up to 20 most recent matches

*Edit & delete:*
\u2022 `edit last` \u2014 edit your last entry (pick from list if multiple)
\u2022 `delete last` / `undo` \u2014 delete last entry

*Settings:*
\u2022 `currency INR` \u2014 set default to Rupees
\u2022 `currency USD` \u2014 set default to Dollars
\u2022 `currency EUR` \u2014 set default to Euros
\u2022 `budget 10000` \u2014 set monthly budget (alerts at 50%, 80%, 100%)
\u2022 `budget off` \u2014 remove budget
\u2022 `notify 9pm` \u2014 daily reminder at 9 PM (your timezone)
\u2022 `notify 9pm EST` \u2014 set time with timezone
\u2022 `notify off` \u2014 turn off reminder
\u2022 `timezone IST` \u2014 set your timezone (IST, EST, SGT\u2026)

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


def _budget_status_line(spent: float, budget: float, currency: str) -> str:
    """Return a single-line budget summary with a text progress bar."""
    pct = spent / budget
    filled = min(int(pct * 10), 10)
    bar = "\u2593" * filled + "\u2591" * (10 - filled)
    remaining = budget - spent
    if remaining >= 0:
        return (
            f"\n\n*Budget: {format_amount(budget, currency)}*\n"
            f"{bar} {pct:.0%} used \u2014 {format_amount(remaining, currency)} left"
        )
    over = abs(remaining)
    return (
        f"\n\n*Budget: {format_amount(budget, currency)}*\n"
        f"{bar} {pct:.0%} used \u2014 *{format_amount(over, currency)} over budget*"
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


async def handle_voice_message(phone_number: str, media_id: str):
    try:
        from .voice import download_audio, transcribe

        audio_bytes = await download_audio(media_id)
        if not audio_bytes:
            await send_text(phone_number, "Couldn't download your voice note. Please try again.")
            return

        transcript = await transcribe(audio_bytes)
        if not transcript:
            await send_text(phone_number, "Couldn't understand that voice note. Try speaking clearly or just type it.")
            return

        await send_text(
            phone_number,
            f"_Heard: {transcript}_\n\nLog this? Reply *yes* to log or *no* to discard."
        )
        set_user_session(phone_number, {"state": "awaiting_voice_confirm", "transcript": transcript})
    except Exception:
        logger.exception("handle_voice_message failed for %s", phone_number)
        try:
            await send_text(phone_number, "Something went wrong processing your voice note. Please try again.")
        except Exception:
            logger.exception("Failed to send voice error message to %s", phone_number)


def _parse_notify_arg(arg: str, stored_tz: str) -> tuple[str, str] | None:
    """Parse 'HH:MM [timezone]' → (hhmm, iana_tz) or None.

    If no timezone is given, falls back to stored_tz.
    Accepts common aliases (IST, EST…) and IANA names (Asia/Kolkata).
    """
    import re
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    parts = arg.strip().split(None, 1)
    time_raw = parts[0].lower().replace(".", ":")
    tz_input = parts[1].strip() if len(parts) > 1 else None

    m = re.match(r'^(\d{1,2})(?::(\d{2}))?(am|pm)?$', time_raw)
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
    hhmm = f"{hour:02d}:{minute:02d}"

    if tz_input is None:
        return hhmm, stored_tz

    iana = TIMEZONE_ALIASES.get(tz_input.upper(), tz_input)
    try:
        ZoneInfo(iana)
        return hhmm, iana
    except (ZoneInfoNotFoundError, KeyError):
        return None


async def handle_message(phone_number: str, message_text: str, from_voice: bool = False):
    try:
        await _handle_message(phone_number, message_text, from_voice)
    except Exception:
        logger.exception("handle_message failed for %s", phone_number)
        try:
            await send_text(phone_number, "Something went wrong on my end. Please try again in a moment.")
        except Exception:
            logger.exception("Failed to send error message to %s", phone_number)


async def _handle_message(phone_number: str, message_text: str, from_voice: bool = False):
    if _check_rate_limit(phone_number):
        await send_text(phone_number, "You're sending messages too fast. Please wait a moment and try again.")
        return

    text = message_text.strip()
    text_lower = text.lower()

    user = get_or_create_user(phone_number)
    session = user.get("session_state") or {"state": "idle"}

    # --- Awaiting voice note confirmation ---
    if session.get("state") == "awaiting_voice_confirm":
        transcript = session.get("transcript", "")
        if text_lower in ("yes", "y", "yep", "yeah", "ok", "okay", "confirm", "log"):
            clear_user_session(phone_number)
            await _handle_message(phone_number, transcript, from_voice=True)
        else:
            clear_user_session(phone_number)
            await send_text(phone_number, "Voice note discarded.")
        return

    # --- Awaiting edit confirmation ---
    if session.get("state") == "awaiting_edit":
        expense_id = session.get("expense_id")
        if expense_id:
            if text_lower == "cancel":
                clear_user_session(phone_number)
                await send_text(phone_number, "Edit cancelled.")
                return
            parsed = parse_expenses(text, user["default_currency"])
            if parsed:
                e = parsed[0]
                update_expense(expense_id, {
                    "amount": e.amount,
                    "currency": e.currency,
                    "description": e.description,
                    "category": e.category
                })
                clear_user_session(phone_number)
                await send_text(phone_number,
                    f"\u2713 Updated: {format_amount(e.amount, e.currency)} \u2014 {e.description} ({e.category})")
            else:
                await send_text(phone_number, "Couldn't parse that. Try: `3500 bike repair`\n\nSend `cancel` to abort.")
        return

    # --- Awaiting batch-edit selection (user picks which expense to edit) ---
    if session.get("state") == "awaiting_edit_select":
        expense_ids = session.get("expense_ids", [])
        expenses = session.get("expenses", [])
        if text_lower == "cancel":
            clear_user_session(phone_number)
            await send_text(phone_number, "Edit cancelled.")
            return
        try:
            idx = int(text.strip()) - 1
            if 0 <= idx < len(expense_ids):
                e = expenses[idx]
                set_user_session(phone_number, {"state": "awaiting_edit", "expense_id": expense_ids[idx]})
                await send_text(
                    phone_number,
                    f"Editing: *{format_amount(e['amount'], e['currency'])}* \u2014 {e['description']} ({e['category']})\n\n"
                    f"Send the corrected entry (or `cancel`):"
                )
            else:
                await send_text(
                    phone_number,
                    f"Please reply with a number between 1 and {len(expense_ids)}, or `cancel`."
                )
        except ValueError:
            await send_text(
                phone_number,
                f"Please reply with a number between 1 and {len(expense_ids)}, or `cancel`."
            )
        return

    # --- Awaiting clear expenses confirmation ---
    if session.get("state") == "awaiting_clear_confirm":
        if text == "CONFIRM CLEAR":
            clear_all_expenses(user["id"])
            clear_user_session(phone_number)
            await send_text(phone_number, "All your expense data has been wiped.")
        else:
            clear_user_session(phone_number)
            await send_text(phone_number, "Cancelled. Your data is safe.")
        return

    # --- Awaiting delete account confirmation ---
    if session.get("state") == "awaiting_delete_confirm":
        if text == "CONFIRM DELETE":
            delete_user(phone_number)
            await send_text(phone_number, "Your account and all expense data have been permanently deleted.")
        else:
            clear_user_session(phone_number)
            await send_text(phone_number, "Cancelled. Your account is safe.")
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

    # --- Timezone command ---
    if text_lower.startswith("timezone"):
        tz_input = text[len("timezone"):].strip()
        if not tz_input:
            current = user.get("notify_timezone") or "Asia/Kolkata"
            alias = _TZ_REVERSE.get(current, current)
            await send_text(phone_number, f"Your timezone is currently *{alias}* ({current}).\n\nChange it with `timezone IST`, `timezone EST`, etc.")
            return
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        iana = TIMEZONE_ALIASES.get(tz_input.upper(), tz_input)
        try:
            ZoneInfo(iana)
            set_user_timezone(phone_number, iana)
            alias = _TZ_REVERSE.get(iana, iana)
            await send_text(phone_number, f"Timezone set to *{alias}* ({iana}).\nYour reminders will now fire in this timezone.")
        except (ZoneInfoNotFoundError, KeyError):
            await send_text(
                phone_number,
                "Couldn't find that timezone.\n\nTry:\n"
                "• `timezone IST` — India\n• `timezone EST` — US East\n"
                "• `timezone SGT` — Singapore\n• `timezone Asia/Kolkata` — IANA name"
            )
        return

    # --- Notify command ---
    if text_lower.startswith("notify"):
        arg = text_lower[len("notify"):].strip()
        if arg == "off":
            clear_notify_time(phone_number)
            await send_text(phone_number, "Daily reminder turned off.")
        else:
            stored_tz = user.get("notify_timezone") or "Asia/Kolkata"
            result = _parse_notify_arg(arg, stored_tz)
            if result:
                hhmm, iana = result
                set_notify_time(phone_number, hhmm, iana)
                alias = _TZ_REVERSE.get(iana, iana)
                await send_text(
                    phone_number,
                    f"Done! I'll remind you every day at *{hhmm} {alias}* ({iana}).\n\nType `notify off` to cancel."
                )
            else:
                await send_text(
                    phone_number,
                    "Couldn't read that.\n\nTry:\n"
                    "• `notify 9pm` — uses your saved timezone\n"
                    "• `notify 9pm IST` — Indian Standard Time\n"
                    "• `notify 9pm EST` — US Eastern\n"
                    "• `notify 9pm Asia/Singapore` — IANA name\n"
                    "• `notify off` — disable"
                )
        return

    # --- Budget command ---
    if text_lower.startswith("budget"):
        arg = text_lower[len("budget"):].strip()
        if arg in ("off", "clear", "remove"):
            clear_budget(phone_number)
            await send_text(phone_number, "Monthly budget removed.")
        else:
            # Parse amount — support "10k", "10000", "10,000"
            import re
            clean = re.sub(r"[,\s]", "", arg)
            clean = re.sub(r"k$", "000", clean)
            try:
                amount = float(clean)
                if amount <= 0:
                    raise ValueError
                set_budget(phone_number, amount)
                cur = user["default_currency"]
                await send_text(
                    phone_number,
                    f"Monthly budget set to *{format_amount(amount, cur)}*.\n\n"
                    f"I'll alert you at 50%, 80%, and 100%.\n"
                    f"_Note: only {cur} expenses count toward this budget._"
                )
            except (ValueError, AttributeError):
                await send_text(
                    phone_number,
                    "Couldn't read that amount.\n\nTry:\n"
                    "• `budget 10000`\n• `budget 10k`\n• `budget off` to remove"
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
        set_user_session(phone_number, {"state": "awaiting_clear_confirm"})
        await send_text(
            phone_number,
            "This will *permanently wipe all your expense data*. This cannot be undone.\n\n"
            "Type *CONFIRM CLEAR* to proceed, or anything else to cancel."
        )
        return

    # --- Delete account ---
    if text_lower in ("delete my account", "delete account"):
        set_user_session(phone_number, {"state": "awaiting_delete_confirm"})
        await send_text(
            phone_number,
            "This will *permanently delete* your account and all expense data. This cannot be undone.\n\n"
            "Type *CONFIRM DELETE* to proceed, or anything else to cancel."
        )
        return

    # --- Edit last ---
    if text_lower == "edit last":
        batch = get_last_batch_expenses(user["id"])
        if not batch:
            await send_text(phone_number, "No recent expense to edit.")
            return
        if len(batch) == 1:
            e = batch[0]
            set_user_session(phone_number, {"state": "awaiting_edit", "expense_id": e["id"]})
            await send_text(
                phone_number,
                f"Last entry: *{format_amount(e['amount'], e['currency'])}* \u2014 {e['description']} ({e['category']})\n\n"
                f"Send the corrected entry (or `cancel`):"
            )
        else:
            lines = [
                f"{i + 1}. {format_amount(e['amount'], e['currency'])} \u2014 {e['description']} _{e['category']}_"
                for i, e in enumerate(batch)
            ]
            set_user_session(phone_number, {
                "state": "awaiting_edit_select",
                "expense_ids": [e["id"] for e in batch],
                "expenses": batch,
            })
            await send_text(
                phone_number,
                f"Your last entry had *{len(batch)} expenses*:\n\n" +
                "\n".join(lines) +
                "\n\nReply with a number to edit that one (e.g. `1` or `2`), or `cancel`."
            )
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
        reply = _format_summary(expenses, today.strftime("%B %Y"))
        budget = user.get("monthly_budget")
        if budget:
            cur = user["default_currency"]
            spent = sum(e["amount"] for e in expenses if e["currency"] == cur)
            reply += _budget_status_line(spent, float(budget), cur)
        await send_text(phone_number, reply)
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

        if "csv" in text_lower or "excel" in text_lower:
            xlsx_data = generate_excel_report(expenses, first_day)
            await send_document(
                phone_number,
                f"kanak_{first_day.strftime('%Y_%m')}.xlsx",
                xlsx_data,
                f"Expenses \u2014 {month_label}",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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

    # --- Search expenses ---
    if text_lower.startswith("search "):
        query = text[len("search "):].strip()
        if not query:
            await send_text(phone_number, "Try: `search netflix` or `search petrol`")
            return
        results = search_expenses(user["id"], query)
        if not results:
            await send_text(phone_number, f'No expenses found matching *"{query}"*.')
            return
        count = len(results)
        header = f'*Search: "{query}"* — {count} result{"s" if count != 1 else ""}\n\n'
        await send_text(phone_number, header + _format_expense_list(results))
        return

    # --- Default: parse as expense ---
    is_first = get_last_expense(user["id"]) is None
    parsed = parse_expenses(text, user["default_currency"], from_voice=from_voice)

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

    # --- Budget threshold alerts ---
    budget = user.get("monthly_budget")
    if budget:
        budget = float(budget)
        cur = user["default_currency"]
        start = date.today().replace(day=1)
        month_expenses = get_expenses_for_period(user["id"], start, date.today())
        new_total = sum(e["amount"] for e in month_expenses if e["currency"] == cur)
        just_added = sum(e.amount for e in parsed if e.currency == cur)
        old_total = new_total - just_added

        thresholds = [
            (1.0, f"You've hit your *{format_amount(budget, cur)} budget* for {date.today().strftime('%B')}! Time to review your spending."),
            (0.8, f"Heads up — you've used *80%* of your {date.today().strftime('%B')} budget ({format_amount(budget, cur)})."),
            (0.5, f"You're halfway through your {date.today().strftime('%B')} budget ({format_amount(budget, cur)})."),
        ]
        for ratio, alert_msg in thresholds:
            if old_total < budget * ratio <= new_total:
                await send_text(phone_number, alert_msg)
                break
