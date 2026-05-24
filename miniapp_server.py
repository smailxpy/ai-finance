import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

from db import get_conn, init_db
from finance import build_snapshot, fmt

load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
APP_SECRET = os.getenv("APP_SECRET", "dev-secret").strip()

ai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = FastAPI(title="AI Finance Mini App", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="miniapp/static"), name="static")
init_db()


# ── Pydantic models ──────────────────────────────────────────────────────────

class TelegramAuthIn(BaseModel):
    init_data: str


class ExpenseIn(BaseModel):
    category: str
    amount: float = Field(gt=0)


class IncomeIn(BaseModel):
    monthly_income: float = Field(gt=0)


class UnplannedIncomeIn(BaseModel):
    amount: float = Field(gt=0)
    source: str = "unplanned"


class GoalIn(BaseModel):
    goal_text: str


class BudgetIn(BaseModel):
    category: str
    monthly_limit: float = Field(gt=0)


# ── Auth ─────────────────────────────────────────────────────────────────────

def _verify_init_data(init_data: str) -> dict:
    token = BOT_TOKEN.strip()
    if not token:
        raise HTTPException(500, "BOT_TOKEN not configured — check your .env file")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    incoming_hash = pairs.pop("hash", None)
    pairs.pop("signature", None)
    if not incoming_hash:
        raise HTTPException(401, "Missing hash in initData")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, incoming_hash):
        raise HTTPException(401, "initData signature mismatch")
    if int(time.time()) - int(pairs.get("auth_date", 0)) > 86400:
        raise HTTPException(401, "initData expired — reopen the Mini App")
    user = json.loads(pairs.get("user", "{}"))
    if not user.get("id"):
        raise HTTPException(401, "Missing user in initData")
    return {
        "id": int(user["id"]),
        "first_name": user.get("first_name"),
        "username": user.get("username"),
    }


def _issue_token(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time())}"
    sig = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_token(token: str) -> int:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise ValueError
        return int(payload.split(":")[0])
    except Exception:
        raise HTTPException(401, "Invalid or expired session token")


def auth(authorization: str = Header(default="")) -> int:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    return _verify_token(authorization[7:])


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "bot_token_set": bool(BOT_TOKEN),
        "bot_token_prefix": BOT_TOKEN[:8] + "..." if BOT_TOKEN else "MISSING",
        "app_secret_set": bool(APP_SECRET),
    }


# ── Static pages ─────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse("miniapp/static/index.html")


# ── Auth endpoint ─────────────────────────────────────────────────────────────

@app.post("/api/auth/telegram")
def auth_telegram(body: TelegramAuthIn):
    user = _verify_init_data(body.init_data)
    return {"token": _issue_token(user["id"]), "user": user}


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/api/dashboard")
def dashboard(uid: int = Depends(auth)):
    return build_snapshot(uid)


# ── Profile ──────────────────────────────────────────────────────────────────

@app.get("/api/profile")
def get_profile(uid: int = Depends(auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT monthly_income, goal_text FROM profiles WHERE user_id = ?", (uid,)
        ).fetchone()
    if not row:
        return {"monthly_income": None, "goal_text": None}
    return {"monthly_income": row["monthly_income"], "goal_text": row["goal_text"]}


@app.post("/api/profile/income")
def set_income(body: IncomeIn, uid: int = Depends(auth)):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO profiles (user_id, monthly_income) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET monthly_income = excluded.monthly_income",
            (uid, body.monthly_income),
        )
    return {"ok": True}


@app.post("/api/profile/goal")
def set_goal(body: GoalIn, uid: int = Depends(auth)):
    text = body.goal_text.strip()
    if not text:
        raise HTTPException(400, "Goal cannot be empty")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO profiles (user_id, goal_text) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET goal_text = excluded.goal_text",
            (uid, text),
        )
    return {"ok": True}


# ── Budgets ──────────────────────────────────────────────────────────────────

@app.get("/api/budgets")
def get_budgets(uid: int = Depends(auth)):
    snap = build_snapshot(uid)
    cat_map = dict(snap["by_category"])
    items = []
    for cat, lim in snap["budgets"]:
        spent = cat_map.get(cat, 0.0)
        pct = round(min(spent / lim * 100, 100), 1) if lim else 0
        items.append({
            "category": cat,
            "monthly_limit": lim,
            "spent": spent,
            "pct": pct,
            "over": spent > lim,
        })
    return {"items": items}


@app.post("/api/budgets")
def set_budget(body: BudgetIn, uid: int = Depends(auth)):
    category = body.category.strip().lower()
    if not category:
        raise HTTPException(400, "Category is required")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id, category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
            (uid, category, body.monthly_limit),
        )
    return {"ok": True}


# ── Expenses ─────────────────────────────────────────────────────────────────

@app.post("/api/expenses")
def add_expense(body: ExpenseIn, uid: int = Depends(auth)):
    category = body.category.strip().lower()
    if not category:
        raise HTTPException(400, "Category is required")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, datetime('now'))",
            (uid, body.amount, category),
        )
    return {"ok": True}


# ── Unplanned income ─────────────────────────────────────────────────────────

@app.post("/api/incomes/unplanned")
def add_unplanned(body: UnplannedIncomeIn, uid: int = Depends(auth)):
    source = body.source.strip() or "unplanned"
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO incomes (user_id, amount, source, created_at) VALUES (?, ?, ?, datetime('now'))",
            (uid, body.amount, source),
        )
    return {"ok": True}


# ── Transactions ─────────────────────────────────────────────────────────────

@app.get("/api/transactions")
def get_transactions(limit: int = 20, uid: int = Depends(auth)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, category, amount, created_at FROM expenses"
            " WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (uid, min(limit, 50)),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.delete("/api/transactions/last")
def undo_last(uid: int = Depends(auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT 1", (uid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "No transaction to undo")
        conn.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (row["id"], uid))
    return {"ok": True}


# ── Action plan ───────────────────────────────────────────────────────────────

@app.get("/api/plan")
def get_plan(uid: int = Depends(auth)):
    snap = build_snapshot(uid)
    items = []
    for cat, amt in snap["by_category"][:3]:
        cut = round(amt * 0.15)
        items.append({"text": f"Reduce '{cat}' by {fmt(cut)} so'm this week"})
    if snap["projected_balance"] is not None and snap["projected_balance"] < 0:
        weekly = round(abs(snap["projected_balance"]) / 4)
        items.append({"text": f"Free up {fmt(weekly)} so'm/week to eliminate deficit"})
    else:
        items.append({"text": "Maintain current pace — review again in 3 days"})
    return {"items": items}


# ── AI advice ─────────────────────────────────────────────────────────────────

@app.get("/api/advice")
def get_advice(uid: int = Depends(auth)):
    if ai is None:
        raise HTTPException(503, "OPENAI_API_KEY not configured")
    snap = build_snapshot(uid)
    if not snap["by_category"]:
        raise HTTPException(422, "Not enough data — add some expenses first")

    cats = "\n".join(f"  {c}: {a:,.0f}" for c, a in snap["by_category"])
    bdg = "\n".join(f"  {c}: {l:,.0f}" for c, l in snap["budgets"]) or "  (none set)"
    over = "\n".join(f"  {c}: spent {s:,.0f}, limit {l:,.0f}" for c, s, l in snap["overspent"]) or "  (none)"

    prompt = (
        f"User financial profile (currency: Uzbek so'm):\n"
        f"  Monthly income: {snap['monthly_income'] or 'not set'}\n"
        f"  Financial goal: {snap['goal_text'] or 'not set'}\n"
        f"  Month spent so far: {snap['month_spent']:,.0f}\n"
        f"  Projected month-end spend: {snap['projected_eom_spent']:,.0f}\n"
        f"  Projected balance: {snap['projected_balance']}\n\n"
        f"Spending by category:\n{cats}\n\n"
        f"Category budgets:\n{bdg}\n\n"
        f"Budget overruns:\n{over}\n\n"
        "Provide 4 concise bullet points of practical financial advice:\n"
        "- Identify one specific category to reduce first\n"
        "- State one concrete amount to cut (in so'm)\n"
        "- If deficit risk exists, give a 7-day stabilization action\n"
        "- One forward-looking tip aligned with the user's stated goal\n"
        "Be specific, data-driven, and direct."
    )

    try:
        resp = ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=280,
        )
        return {"text": (resp.choices[0].message.content or "").strip()}
    except Exception as e:
        raise HTTPException(503, f"AI service error: {e}")
