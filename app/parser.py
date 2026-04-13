import os
import json
import re
from dotenv import load_dotenv
from groq import Groq
from .models import ParsedExpense

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

client = Groq(api_key=os.environ["GROQ_API_KEY"])

SYSTEM_PROMPT = """You are an expense parsing assistant. Extract ALL expenses from the user's message.

Categories (use exactly these strings): Food, Transport, Entertainment, Health, Shopping, Utilities, Personal Care, Education, Travel, Other

Category hints:
- Food: restaurants, groceries, chai, coffee, lunch, dinner, snacks, swiggy, zomato, food delivery, sabzi, milk
- Transport: fuel, petrol, diesel, uber, ola, auto, bus, train, metro, cab, bike repair, car service, toll, parking
- Entertainment: netflix, amazon prime, hotstar, spotify, movies, games, concerts, OTT subscriptions, youtube premium
- Health: doctor, medicine, pharmacy, hospital, gym, fitness, protein, supplements
- Shopping: clothes, electronics, amazon, flipkart, meesho, online shopping, fashion
- Utilities: electricity, water, internet, phone bill, broadband, gas, wifi, recharge
- Personal Care: haircut, salon, grooming, spa, beauty
- Education: books, courses, tuition, college fees, udemy, coursera
- Travel: flights, hotels, trips, vacation, holiday, airbnb, booking
- Other: anything that doesn't fit above

Currency rules:
- ₹, rs, inr, rupees, rupe, र → INR
- $, usd, dollars, dollar → USD
- No symbol → use {default_currency}
- Amount variants: 4k=4000, 1.5k=1500, 4,000=4000, 4.5k=4500

Return ONLY a valid JSON array, no explanation, no markdown:
[{{"amount": 4000.0, "currency": "INR", "description": "Bike repair", "category": "Transport"}}]

Multiple expenses example:
Input: "4000 bike repair, 199 netflix, $15 spotify"
Output: [{{"amount": 4000.0, "currency": "INR", "description": "Bike repair", "category": "Transport"}}, {{"amount": 199.0, "currency": "INR", "description": "Netflix", "category": "Entertainment"}}, {{"amount": 15.0, "currency": "USD", "description": "Spotify", "category": "Entertainment"}}]

If no valid expense found, return: []"""


def _normalize_amount(raw: str) -> float:
    raw = raw.strip().lower().replace(",", "")
    if raw.endswith("k"):
        return float(raw[:-1]) * 1000
    return float(raw)


def parse_expenses(message: str, default_currency: str = "INR") -> list[ParsedExpense]:
    prompt = SYSTEM_PROMPT.replace("{default_currency}", default_currency)

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": message}
        ],
        temperature=0.1,
        max_tokens=512
    )

    raw = response.choices[0].message.content.strip()

    # Extract JSON array — handle cases where model adds extra text
    match = re.search(r'\[.*?\]', raw, re.DOTALL)
    if not match:
        return []

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    expenses = []
    for item in data:
        try:
            amount = float(item["amount"])
            if amount <= 0:
                continue
            currency = str(item.get("currency", default_currency)).upper()
            if currency not in ("INR", "USD"):
                currency = default_currency
            expenses.append(ParsedExpense(
                amount=amount,
                currency=currency,
                description=str(item["description"]).strip(),
                category=str(item.get("category", "Other")).strip()
            ))
        except (KeyError, ValueError, TypeError):
            continue

    return expenses
