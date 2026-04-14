import os
from supabase import create_client, Client
from datetime import date
from typing import Optional
from .models import ParsedExpense

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)


def get_or_create_user(phone_number: str) -> dict:
    result = supabase.table("users").select("*").eq("phone_number", phone_number).execute()
    if result.data:
        return result.data[0]
    result = supabase.table("users").insert({
        "phone_number": phone_number,
        "default_currency": "INR"
    }).execute()
    return result.data[0]


def set_default_currency(phone_number: str, currency: str):
    supabase.table("users").update({"default_currency": currency}).eq("phone_number", phone_number).execute()


def log_expenses(user_id: str, expenses: list[ParsedExpense], raw_input: str) -> list[dict]:
    records = [
        {
            "user_id": user_id,
            "amount": e.amount,
            "currency": e.currency,
            "description": e.description,
            "category": e.category,
            "expense_date": str(date.today()),
            "raw_input": raw_input,
            **({"inr_equivalent": e.inr_equivalent} if e.inr_equivalent is not None else {})
        }
        for e in expenses
    ]
    result = supabase.table("expenses").insert(records).execute()
    return result.data


def get_last_expense(user_id: str) -> Optional[dict]:
    result = (
        supabase.table("expenses")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def update_expense(expense_id: str, updates: dict):
    supabase.table("expenses").update(updates).eq("id", expense_id).execute()


def delete_expense(expense_id: str):
    supabase.table("expenses").delete().eq("id", expense_id).execute()


def get_expenses_for_period(user_id: str, start_date: date, end_date: date) -> list[dict]:
    result = (
        supabase.table("expenses")
        .select("*")
        .eq("user_id", user_id)
        .gte("expense_date", str(start_date))
        .lte("expense_date", str(end_date))
        .order("expense_date")
        .execute()
    )
    return result.data


def get_all_users() -> list[dict]:
    result = supabase.table("users").select("*").execute()
    return result.data


def clear_all_expenses(user_id: str):
    supabase.table("expenses").delete().eq("user_id", user_id).execute()


def delete_user(phone_number: str):
    """Permanently delete user and all their expenses (CASCADE handles expenses)."""
    supabase.table("users").delete().eq("phone_number", phone_number).execute()


def set_notify_time(phone_number: str, hhmm: str):
    """Store HH:MM (IST, 24h) notify time for a user."""
    supabase.table("users").update({"notify_time": hhmm}).eq("phone_number", phone_number).execute()


def clear_notify_time(phone_number: str):
    supabase.table("users").update({"notify_time": None}).eq("phone_number", phone_number).execute()


def get_users_to_notify(hhmm: str) -> list[dict]:
    """Return users whose notify_time matches HH:MM."""
    result = (
        supabase.table("users")
        .select("phone_number, display_name")
        .eq("notify_time", hhmm)
        .execute()
    )
    return result.data
