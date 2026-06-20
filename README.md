# AI-Powered Personal Finance Assistant

A production-grade AI-powered personal finance web application with Telegram bot integration.

## Features

- **Expense tracking** — log expenses by category with multi-word support
- **Budget control** — set monthly limits per category with visual progress bars
- **Deficit forecasting** — projects end-of-month balance and warns before it's too late
- **Unplanned income** — track bonuses, freelance, and other irregular income
- **Financial goals** — set and track personalized goals used by the AI advisor
- **AI advice** — OpenAI-powered recommendations grounded in your actual data
- **7-day action plan** — deterministic spending cuts based on your top categories
- **Transaction history** — recent ledger with relative timestamps and undo support
- **Secure auth** — Telegram `initData` HMAC verification, no passwords

## Tech Stack

| Layer | Technology |
|---|---|
| Bot | Python · aiogram 3 |
| API server | FastAPI · uvicorn |
| Database | SQLite (WAL mode) |
| AI | OpenAI gpt-4o-mini |
| Frontend | Vanilla JS · CSS3 (glassmorphism) |
| Tunnel (dev) | ngrok |

## Project Structure

```
ai-finance/
├── db.py                  # DB context manager + schema init
├── finance.py             # Shared business logic (snapshot, forecasting)
├── bot.py                 # Telegram bot — Mini App launcher
├── miniapp_server.py      # FastAPI REST API (14 endpoints)
├── miniapp/static/
│   ├── index.html         # Dashboard — metrics + budget bars + risk banner
│   ├── money.html         # Money Actions — expense, income, budget, unplanned
│   ├── goals.html         # Goals — set and view current goal
│   ├── advice.html        # AI Advice — action plan + AI report
│   ├── transactions.html  # Ledger — history, search, undo
│   ├── common.js          # Shared auth, toast, timeAgo, API wrapper
│   └── styles.css         # Design system — tokens, glass, progress, toasts
├── requirements-miniapp.txt
└── .env.example
```

## Setup

### 1. Clone and install

```bash
git clone https://github.com/smailxpy/ai-finance.git
cd ai-finance
python -m venv venv
source venv/bin/activate
pip install -r requirements-miniapp.txt
pip install aiogram
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
BOT_TOKEN=your_telegram_bot_token
OPENAI_API_KEY=your_openai_api_key
WEBAPP_URL=https://your-ngrok-url.ngrok-free.app
APP_SECRET=any-random-secret-string
```

Get a bot token from [@BotFather](https://t.me/BotFather) on Telegram.

### 3. Run locally (3 terminals)

**Terminal 1 — expose with ngrok:**
```bash
ngrok http 8000
# copy the https://... URL into WEBAPP_URL in .env
```

**Terminal 2 — start the API server:**
```bash
source venv/bin/activate
uvicorn miniapp_server:app --reload --port 8000
```

**Terminal 3 — start the bot:**
```bash
source venv/bin/activate
python bot.py
```

Then open your bot in Telegram and send `/start`.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/telegram` | Verify Telegram initData → session token |
| GET | `/api/dashboard` | Full financial snapshot |
| GET | `/api/profile` | Monthly income + goal |
| POST | `/api/profile/income` | Set monthly income |
| POST | `/api/profile/goal` | Set financial goal |
| GET | `/api/budgets` | Budgets with spend % |
| POST | `/api/budgets` | Create/update budget |
| POST | `/api/expenses` | Add expense |
| POST | `/api/incomes/unplanned` | Add unplanned income |
| GET | `/api/transactions` | Recent transactions |
| DELETE | `/api/transactions/last` | Undo last transaction |
| GET | `/api/plan` | 7-day action plan |
| GET | `/api/advice` | AI-generated advice |

## License

MIT


![image alt](https://github.com/smailxpy/ai-finance/blob/main/DEMO.png?raw=true)
