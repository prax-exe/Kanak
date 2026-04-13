from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional
from enum import Enum


class Currency(str, Enum):
    INR = "INR"
    USD = "USD"


class Category(str, Enum):
    FOOD = "Food"
    TRANSPORT = "Transport"
    ENTERTAINMENT = "Entertainment"
    HEALTH = "Health"
    SHOPPING = "Shopping"
    UTILITIES = "Utilities"
    PERSONAL_CARE = "Personal Care"
    EDUCATION = "Education"
    TRAVEL = "Travel"
    OTHER = "Other"


class ParsedExpense(BaseModel):
    amount: float
    currency: str
    description: str
    category: str


class Expense(BaseModel):
    id: str
    user_id: str
    amount: float
    currency: str
    description: str
    category: str
    expense_date: date
    raw_input: Optional[str] = None
    created_at: datetime


class User(BaseModel):
    id: str
    phone_number: str
    display_name: Optional[str] = None
    default_currency: str
    created_at: datetime
