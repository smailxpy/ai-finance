# Telegram Mini App Setup

## 1) Environment
Add these to `.env`:

- `BOT_TOKEN=...`
- `OPENAI_API_KEY=...` (optional for AI advice)
- `WEBAPP_URL=https://<your-domain>`
- `APP_SECRET=<long-random-secret>`

## 2) Run API + Web App

```bash
python3 -m uvicorn miniapp_server:app --host 0.0.0.0 --port 8000 --reload
```

## 3) Expose HTTPS URL
Telegram Mini Apps require HTTPS. Use a public HTTPS domain (or tunnel in dev).
Set that URL as `WEBAPP_URL`.

## 4) Telegram Bot Button
The bot now includes an `Open Mini App` button using `web_app`.
You can also configure the chat menu button in BotFather:

- `/setmenubutton`
- Choose your bot
- Set button text and same `WEBAPP_URL`

## 5) Auth Verification (Implemented)
Backend endpoint `POST /api/auth/telegram` verifies `initData` with HMAC-SHA256.

Validation logic used:
- Parse key/value pairs from `initData`
- Remove `hash` and `signature`
- Build `data_check_string` by sorting keys and joining `key=value` with `\n`
- `secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN)`
- `calculated_hash = HMAC_SHA256(key=secret_key, msg=data_check_string)`
- Compare with incoming `hash` using constant-time compare
- Enforce `auth_date` freshness (24h)

## 6) UI/UX Scope Delivered
- Single-page Mini App dashboard
- Quick add expense
- Income, goal, and budget setup
- Action plan and AI advice
- Recent transactions + undo last
- Mobile-first responsive layout

