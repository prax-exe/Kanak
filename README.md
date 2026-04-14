# Kanak — WhatsApp Expense Tracker

A self-hosted WhatsApp bot that lets you log, view, and report expenses using natural language. Powered by Meta WhatsApp Cloud API, Groq (Llama 3.1), and Supabase.

---

## Features

- Log expenses by just typing naturally — `coffee 50`, `4k rent`, `$15 netflix`
- Multi-currency — INR, USD, EUR with live INR equivalent stored at log time
- Auto-categorization via LLM (Food, Transport, Entertainment, and more)
- Summaries — `today`, `week`, `month`
- PDF and CSV reports on demand
- Monthly auto-report via GitHub Actions cron
- Edit and delete last expense
- Zero app install for users — works entirely over WhatsApp

---

## Tech Stack

| Layer | Tool |
|---|---|
| Bot runtime | Python + FastAPI |
| LLM parsing | Groq API (Llama 3.1 8B Instant) |
| Database | Supabase (Postgres) |
| WhatsApp API | Meta WhatsApp Cloud API |
| Hosting | Railway / any Docker host |
| Reports | ReportLab (PDF), csv (CSV) |

---

## Self-Host Setup

### Prerequisites

- Python 3.11+
- A [Meta Developer](https://developers.facebook.com) account
- A [Supabase](https://supabase.com) project
- A [Groq](https://console.groq.com) API key

---

### Step 1 — Clone the repo

```bash
git clone https://github.com/prax-exe/Kanak.git
cd Kanak
pip install -r requirements.txt
```

---

### Step 2 — Set up Supabase

1. Create a new project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and run the contents of `schema.sql`
3. Copy your **Project URL** and **service_role** key from Settings → API

---

### Step 3 — Get a Groq API key

1. Sign up at [console.groq.com](https://console.groq.com)
2. Create an API key

---

### Step 4 — Set up Meta WhatsApp

1. Go to [developers.facebook.com](https://developers.facebook.com) → Create App → Business
2. Add the **WhatsApp** product
3. Under **API Setup**, note your:
   - **Phone Number ID**
   - **Temporary Access Token** (or generate a permanent one via System User — recommended)
4. Add your personal WhatsApp number as a **test recipient**

---

### Step 5 — Configure environment variables

Create a `.env` file in the project root:

```env
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_ACCESS_TOKEN=your_access_token
WHATSAPP_VERIFY_TOKEN=any_string_you_choose
GROQ_API_KEY=your_groq_api_key
SUPABASE_URL=your_supabase_project_url
SUPABASE_SERVICE_KEY=your_supabase_service_role_key
SCHEDULER_SECRET=any_string_you_choose
```

| Variable | Where to find it |
|---|---|
| `WHATSAPP_PHONE_NUMBER_ID` | Meta → WhatsApp → API Setup |
| `WHATSAPP_ACCESS_TOKEN` | Meta → WhatsApp → API Setup |
| `WHATSAPP_VERIFY_TOKEN` | Any string — must match what you enter in Meta webhook config |
| `GROQ_API_KEY` | Groq console |
| `SUPABASE_URL` | Supabase → Settings → API |
| `SUPABASE_SERVICE_KEY` | Supabase → Settings → API → service_role |
| `SCHEDULER_SECRET` | Any string — protects the monthly report endpoint |

---

### Step 6 — Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

To expose it to Meta's webhook, use [ngrok](https://ngrok.com):

```bash
ngrok http 8000
```

Copy the HTTPS URL (e.g. `https://xxxx.ngrok.io`) and go to:

Meta Dashboard → WhatsApp → Configuration → Webhook

Set:
- **Callback URL:** `https://xxxx.ngrok.io/webhook`
- **Verify Token:** same value as `WHATSAPP_VERIFY_TOKEN` in your `.env`

Click **Verify and Save**, then subscribe to the `messages` field.

---

### Step 7 — Deploy (Railway)

1. Push your code to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add all environment variables under the **Variables** tab
4. Go to **Settings → Networking → Generate Domain** to get your public URL
5. Update the Meta webhook URL to your Railway domain

The repo includes a `Dockerfile` — Railway picks it up automatically.

---

### Step 8 — Monthly auto-report (optional)

The repo includes a GitHub Actions workflow (`.github/workflows/monthly_report.yml`) that triggers a PDF report for all users on the 1st of every month.

Add these secrets to your GitHub repo under Settings → Secrets → Actions:

| Secret | Value |
|---|---|
| `APP_URL` | Your deployed URL e.g. `https://kanak-production.up.railway.app` |
| `SCHEDULER_SECRET` | Same value as `SCHEDULER_SECRET` in your `.env` |

---

## Bot Commands

| Message | Action |
|---|---|
| `coffee 50` | Log ₹50 under Food |
| `4000 bike repair` | Log ₹4000 under Transport |
| `199 netflix, 800 groceries` | Log two expenses at once |
| `$15 spotify` | Log in USD (INR equivalent saved automatically)|
| `€10 museum` | Log in EUR (INR equivalent saved automatically) |
| `4k rent` | k = thousands |
| `today` | Today's expenses |
| `week` | This week summary |
| `month` | This month summary |
| `report` | PDF report for this month |
| `report csv` | CSV report for this month |
| `report last month` | PDF for previous month |
| `edit last` | Edit your last entry |
| `delete last` | Delete your last entry |
| `currency INR` / `USD` / `EUR` | Set your default currency |
| `help` | Show all commands |

---

## Permanent Access Token (recommended)

The default Meta access token expires every 24 hours. To generate one that never expires:

1. Go to [business.facebook.com](https://business.facebook.com) → Settings → Users → System Users
2. Create a System User with **Admin** role
3. Click **Assign Assets** → Apps → your app → Full Control
4. Click **Generate New Token** → select your app → enable `whatsapp_business_messaging` → set expiry to **Never**

---

## License

MIT


## License

MIT
