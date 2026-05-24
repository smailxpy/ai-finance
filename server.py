import hashlib
import hmac
import json
import os
import re
import time
import urllib.request
from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

from db import get_conn, init_db
from finance import build_snapshot, fmt

load_dotenv(override=True)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "").strip()
APP_SECRET         = os.getenv("APP_SECRET", "dev-secret").strip()
_BOT_TOKEN         = os.getenv("BOT_TOKEN", "").strip()

# Prefer OpenRouter; fall back to direct OpenAI
if OPENROUTER_API_KEY:
    ai = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    _AI_MODEL = "openai/gpt-4o-mini"
elif OPENAI_API_KEY and OPENAI_API_KEY != "your_openai_api_key":
    ai = OpenAI(api_key=OPENAI_API_KEY)
    _AI_MODEL = "openai/gpt-4o-mini"
else:
    ai = None
    _AI_MODEL = ""

# Resolve bot username once at startup (best-effort, never crashes)
_bot_username: str = ""
if _BOT_TOKEN:
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{_BOT_TOKEN}/getMe", timeout=4
        ) as _r:
            _bot_username = json.loads(_r.read())["result"].get("username", "")
    except Exception:
        pass

app = FastAPI(title="AI Finance", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="miniapp/static"), name="static")
init_db()


# ── Password hashing (stdlib only, no extra packages) ─────────────────────────

def _hash_password(password: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ── Session tokens ────────────────────────────────────────────────────────────

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


# ── Pydantic models ───────────────────────────────────────────────────────────

class RegisterIn(BaseModel):
    username: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class ExpenseIn(BaseModel):
    category: str
    amount: float = Field(gt=0)
    date: Optional[str] = None


class IncomeIn(BaseModel):
    monthly_income: float = Field(gt=0)


class UnplannedIncomeIn(BaseModel):
    amount: float = Field(gt=0)
    source: str = "unplanned"


class GoalTextIn(BaseModel):
    goal_text: str


class GoalIn(BaseModel):
    title: str
    category: str = "general"
    target_amount: float = Field(gt=0)
    deadline: Optional[str] = None


class NetWorthIn(BaseModel):
    net_worth: float = Field(gt=0)


class GoalContribIn(BaseModel):
    amount: float = Field(gt=0)


class BudgetIn(BaseModel):
    category: str
    monthly_limit: float = Field(gt=0)


class ChatIn(BaseModel):
    message: str
    history: list = []


# ── Public config ─────────────────────────────────────────────────────────────

@app.get("/api/config")
def app_config():
    return {"bot_username": _bot_username}


# ── Static pages ──────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse("miniapp/static/index.html")


@app.get("/login")
def login_page():
    return FileResponse("miniapp/static/login.html")


@app.get("/register")
def register_page():
    return FileResponse("miniapp/static/register.html")


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
def register(body: RegisterIn):
    username = body.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters")
    if not username.replace("_", "").isalnum():
        raise HTTPException(400, "Username can only contain letters, numbers, and underscores")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            raise HTTPException(409, "Username already taken")
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, _hash_password(body.password)),
        )
        user = conn.execute(
            "SELECT id, username FROM users WHERE username = ?", (username,)
        ).fetchone()

    return {"token": _issue_token(user["id"]), "user": {"id": user["id"], "username": user["username"]}}


@app.post("/api/auth/login")
def login(body: LoginIn):
    username = body.username.strip().lower()
    with get_conn() as conn:
        user = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not user or not _verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid username or password")
    return {"token": _issue_token(user["id"]), "user": {"id": user["id"], "username": user["username"]}}


@app.get("/api/auth/me")
def me(uid: int = Depends(auth)):
    with get_conn() as conn:
        user = conn.execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
    if not user:
        raise HTTPException(404, "User not found")
    return {"id": user["id"], "username": user["username"]}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/api/dashboard")
def dashboard(uid: int = Depends(auth)):
    return build_snapshot(uid)


# ── Profile ───────────────────────────────────────────────────────────────────

@app.get("/api/profile")
def get_profile(uid: int = Depends(auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT monthly_income, goal_text, net_worth FROM profiles WHERE user_id = ?", (uid,)
        ).fetchone()
    if not row:
        return {"monthly_income": None, "goal_text": None, "net_worth": None}
    return {"monthly_income": row["monthly_income"], "goal_text": row["goal_text"], "net_worth": row["net_worth"]}


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
def set_goal(body: GoalTextIn, uid: int = Depends(auth)):
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


@app.post("/api/profile/net-worth")
def set_net_worth(body: NetWorthIn, uid: int = Depends(auth)):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO profiles (user_id, net_worth) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET net_worth = excluded.net_worth",
            (uid, body.net_worth),
        )
    return {"ok": True}


# ── Budgets ───────────────────────────────────────────────────────────────────

@app.get("/api/budgets")
def get_budgets(uid: int = Depends(auth)):
    snap = build_snapshot(uid)
    cat_map = dict(snap["by_category"])
    items = []
    for cat, lim in snap["budgets"]:
        spent = cat_map.get(cat, 0.0)
        pct = round(min(spent / lim * 100, 100), 1) if lim else 0
        items.append({"category": cat, "monthly_limit": lim, "spent": spent, "pct": pct, "over": spent > lim})
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


# ── Expenses ──────────────────────────────────────────────────────────────────

@app.post("/api/expenses")
def add_expense(body: ExpenseIn, uid: int = Depends(auth)):
    category = body.category.strip().lower()
    if not category:
        raise HTTPException(400, "Category is required")
    # Allow optional past date; default to now
    if body.date:
        try:
            from datetime import datetime as _dt
            created_at = _dt.fromisoformat(body.date.strip()).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            created_at = None
    else:
        created_at = None
    with get_conn() as conn:
        if created_at:
            conn.execute(
                "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, ?)",
                (uid, body.amount, category, created_at),
            )
        else:
            conn.execute(
                "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, datetime('now'))",
                (uid, body.amount, category),
            )
    return {"ok": True}


# ── Unplanned income ──────────────────────────────────────────────────────────

@app.post("/api/incomes/unplanned")
def add_unplanned(body: UnplannedIncomeIn, uid: int = Depends(auth)):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO incomes (user_id, amount, source, created_at) VALUES (?, ?, ?, datetime('now'))",
            (uid, body.amount, body.source.strip() or "unplanned"),
        )
    return {"ok": True}


# ── Transactions ──────────────────────────────────────────────────────────────

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


# ── Goals ────────────────────────────────────────────────────────────────────

@app.get("/api/goals")
def get_goals(uid: int = Depends(auth)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, category, target_amount, saved_amount, deadline, created_at FROM goals WHERE user_id = ? ORDER BY created_at DESC",
            (uid,),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/goals")
def add_goal(body: GoalIn, uid: int = Depends(auth)):
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title is required")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goals (user_id, title, category, target_amount, deadline) VALUES (?, ?, ?, ?, ?)",
            (uid, title, body.category.strip() or "general", body.target_amount, body.deadline),
        )
    return {"ok": True}


@app.post("/api/goals/{goal_id}/contribute")
def contribute_goal(goal_id: int, body: GoalContribIn, uid: int = Depends(auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, title, target_amount, saved_amount FROM goals WHERE id = ? AND user_id = ?", (goal_id, uid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Goal not found")
        new_saved = min(row["saved_amount"] + body.amount, row["target_amount"])
        actual = new_saved - row["saved_amount"]  # capped contribution
        conn.execute(
            "UPDATE goals SET saved_amount = ? WHERE id = ? AND user_id = ?",
            (new_saved, goal_id, uid),
        )
        # Deduct from available balance by recording as expense
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, datetime('now'))",
            (uid, actual, "goal savings"),
        )
    return {"ok": True, "saved_amount": new_saved}


@app.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int, uid: int = Depends(auth)):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM goals WHERE id = ? AND user_id = ?", (goal_id, uid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Goal not found")
        conn.execute("DELETE FROM goals WHERE id = ? AND user_id = ?", (goal_id, uid))
    return {"ok": True}


# ── Seed sample data ──────────────────────────────────────────────────────────

@app.post("/api/seed")
def seed_data(uid: int = Depends(auth)):
    today = date.today()

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE user_id = ?", (uid,)
        ).fetchone()[0]
        if existing > 5:
            return {"ok": True, "skipped": True, "message": "Data already exists"}

    # 3 months of realistic Uzbek-market spending (so'm)
    # Each entry: (category, amount, days_ago)
    template = [
        ("rent",          3_000_000, 1),
        ("food",            320_000, 3),
        ("groceries",       480_000, 7),
        ("restaurant",      250_000, 10),
        ("coffee",           90_000, 11),
        ("electricity",     180_000, 12),
        ("internet",        120_000, 15),
        ("transport",       200_000, 16),
        ("taxi",            100_000, 18),
        ("shopping",        750_000, 20),
        ("health",          150_000, 22),
        ("gym",             100_000, 23),
        ("entertainment",   200_000, 25),
        ("food",            280_000, 27),
    ]

    rows = []
    for month_offset in range(3, 0, -1):
        # Approximate "days ago" as month_offset * 30 + day_within_month
        base_days = month_offset * 30
        for cat, amt, day_in_month in template:
            days_ago = base_days + (30 - day_in_month)
            tx_date = today - timedelta(days=days_ago)
            rows.append((uid, amt, cat, tx_date.strftime("%Y-%m-%d 12:00:00")))

    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO expenses (user_id, amount, category, created_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        # Set income + net worth if not already set
        conn.execute(
            "INSERT INTO profiles (user_id, monthly_income, net_worth) VALUES (?, 20000000, 120000000)"
            " ON CONFLICT(user_id) DO UPDATE SET"
            "   monthly_income = COALESCE(monthly_income, 20000000),"
            "   net_worth      = COALESCE(net_worth, 120000000)",
            (uid,),
        )
        # Seed default budgets
        default_budgets = [
            ("rent", 3_500_000), ("food", 1_000_000), ("groceries", 600_000),
            ("transport", 400_000), ("shopping", 800_000), ("health", 300_000),
            ("entertainment", 300_000), ("gym", 150_000),
        ]
        for cat, lim in default_budgets:
            conn.execute(
                "INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?, ?, ?)"
                " ON CONFLICT(user_id, category) DO NOTHING",
                (uid, cat, lim),
            )

    return {"ok": True, "inserted": len(rows)}


# ── AI advice (structured) ─────────────────────────────────────────────────────

def _parse_ai_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {}


@app.get("/api/advice")
def get_advice(uid: int = Depends(auth)):
    if ai is None:
        raise HTTPException(503, "AI not configured")
    snap = build_snapshot(uid)

    income = snap["monthly_income"] or 0
    spent = snap["month_spent"]
    save_rate = max(0, (income - spent) / income * 100) if income > 0 else 0
    cats = "\n".join(f"  {c}: {a:,.0f}" for c, a in snap["by_category"])
    over = ", ".join(f"{c} (+{s-l:,.0f})" for c, s, l in snap["overspent"]) or "none"

    prompt = (
        f"Personal finance analysis (Uzbek so'm):\n"
        f"  Monthly income: {income:,.0f}, Spent: {spent:,.0f}, Savings rate: {save_rate:.1f}%\n"
        f"  Budget overruns: {over}\n"
        f"  Top spending:\n{cats}\n\n"
        'Return ONLY valid JSON (no markdown, no fences):\n'
        '{"score":72,"grade":"B","headline":"One-line assessment under 12 words",'
        '"sections":['
        '{"icon":"🔴","title":"Top Risk","body":"2-3 sentences with specific so\'m amounts"},'
        '{"icon":"💡","title":"Quick Win","body":"Concrete action with amount"},'
        '{"icon":"🎯","title":"Goal Progress","body":"Forward-looking guidance"},'
        '{"icon":"📈","title":"Month Forecast","body":"End-of-month projection"}],'
        '"next_steps":["Specific action 1","Action 2","Action 3"]}\n'
        "Score 0=critical 100=excellent. Use real numbers from the data."
    )

    try:
        resp = ai.chat.completions.create(
            model=_AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        result = _parse_ai_json(raw)
        if not result:
            result = {"score": 50, "grade": "C", "headline": "Analysis complete", "sections": [], "next_steps": [], "text": raw}
        return result
    except Exception as e:
        raise HTTPException(503, f"AI service error: {e}")


# ── AI chat ────────────────────────────────────────────────────────────────────

@app.post("/api/chat")
def chat(body: ChatIn, uid: int = Depends(auth)):
    if ai is None:
        raise HTTPException(503, "AI not configured")

    snap = build_snapshot(uid)
    income = snap["monthly_income"] or 0
    spent = snap["month_spent"]
    balance = income - spent
    top_cats = ", ".join(f"{c}:{a:,.0f}" for c, a in snap["by_category"][:4])

    system = (
        "You are a friendly, concise personal finance assistant for an Uzbek user. "
        f"Financial context: income={income:,.0f} so'm/month, "
        f"spent_this_month={spent:,.0f} so'm, remaining_balance={balance:,.0f} so'm. "
        f"Top spending: {top_cats}. "
        "Answer in 2-4 sentences. Be direct, helpful, and supportive. "
        "Use so'm for currency. If the user asks something unrelated to finance, gently redirect."
    )

    messages = [{"role": "system", "content": system}]
    for h in (body.history or [])[-8:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": str(h.get("content", ""))})
    messages.append({"role": "user", "content": body.message})

    try:
        resp = ai.chat.completions.create(
            model=_AI_MODEL,
            messages=messages,
            max_tokens=1000,
        )
        return {"reply": (resp.choices[0].message.content or "").strip()}
    except Exception as e:
        raise HTTPException(503, f"AI service error: {e}")


# ── AI expense analysis ────────────────────────────────────────────────────────

@app.post("/api/analyze")
def analyze_expenses(uid: int = Depends(auth)):
    if ai is None:
        raise HTTPException(503, "AI not configured")

    snap = build_snapshot(uid)

    today = date.today()
    last_month_date = (today.replace(day=1) - timedelta(days=1))

    with get_conn() as conn:
        this_rows = conn.execute(
            "SELECT category, SUM(amount) as total FROM expenses "
            "WHERE user_id=? AND strftime('%Y-%m', created_at)=? "
            "GROUP BY category ORDER BY total DESC",
            (uid, today.strftime("%Y-%m")),
        ).fetchall()
        last_rows = conn.execute(
            "SELECT category, SUM(amount) as total FROM expenses "
            "WHERE user_id=? AND strftime('%Y-%m', created_at)=? "
            "GROUP BY category ORDER BY total DESC",
            (uid, last_month_date.strftime("%Y-%m")),
        ).fetchall()

    this = {r["category"]: r["total"] for r in this_rows}
    last = {r["category"]: r["total"] for r in last_rows}

    trends_text = []
    for cat, amt in this.items():
        if cat in last and last[cat] > 0:
            pct = (amt - last[cat]) / last[cat] * 100
            trends_text.append(f"{cat}: {pct:+.0f}% ({last[cat]:,.0f}→{amt:,.0f})")
        else:
            trends_text.append(f"{cat}: new ({amt:,.0f})")

    prompt = (
        f"Analyze this user's spending (so'm).\n"
        f"This month: {', '.join(f'{c}:{v:,.0f}' for c,v in this.items())}\n"
        f"Month-over-month: {'; '.join(trends_text) or 'no prior data'}\n"
        f"Over-budget categories: {len(snap['overspent'])}\n\n"
        'Return ONLY valid JSON (no markdown):\n'
        '{"summary":"2-sentence spending overview",'
        '"alerts":[{"type":"warning","title":"short title","body":"2-sentence detail with amounts"}],'
        '"trends":[{"category":"food","direction":"up","change_pct":18,"text":"Your food expenses increased by 18% this month."}],'
        '"insight":"1 forward-looking action tip"}\n'
        "Include 1-3 alerts (flag unusual spikes, budget breaches, or positives like savings), "
        "2-4 trend items. type can be: warning, danger, success."
    )

    try:
        resp = ai.chat.completions.create(
            model=_AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        result = _parse_ai_json(raw)
        if not result:
            result = {"summary": "", "alerts": [], "trends": [], "insight": ""}
        return result
    except Exception as e:
        raise HTTPException(503, f"AI service error: {e}")


# ── AI budget recommendations ──────────────────────────────────────────────────

@app.post("/api/budget-recommend")
def budget_recommend(uid: int = Depends(auth)):
    if ai is None:
        raise HTTPException(503, "AI not configured")

    snap = build_snapshot(uid)

    income = snap["monthly_income"] or 0
    cats_str = ", ".join(f"{c}:{v:,.0f}" for c, v in snap["by_category"])

    prompt = (
        f"Create budget recommendations (so'm).\n"
        f"Monthly income: {income:,.0f}, Total spent: {snap['month_spent']:,.0f}\n"
        f"Current spending: {cats_str}\n\n"
        'Return ONLY valid JSON (no markdown):\n'
        '{"headline":"You can save X,XXX,XXX so\'m/month by reducing Y",'
        '"recommendations":['
        '{"category":"food","current":500000,"suggested":400000,"savings":100000,'
        '"tip":"One concrete action to reduce this"}],'
        '"total_potential_savings":1200000,'
        '"overall_tip":"One strategic sentence"}\n'
        "Give 3-5 realistic recommendations. Keep suggested >= 60% of current."
    )

    try:
        resp = ai.chat.completions.create(
            model=_AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        raw = (resp.choices[0].message.content or "").strip()
        result = _parse_ai_json(raw)
        if not result:
            result = {"headline": "Analysis complete", "recommendations": [], "total_potential_savings": 0, "overall_tip": raw}
        return result
    except Exception as e:
        raise HTTPException(503, f"AI service error: {e}")
