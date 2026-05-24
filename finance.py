import calendar
from datetime import date, timedelta

from db import get_conn


def fmt(v: float) -> str:
    """Format a number with space-separated thousands."""
    return f"{v:,.0f}".replace(",", " ")


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def build_snapshot(user_id: int) -> dict:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    dim = _days_in_month(today.year, today.month)
    days_passed = today.day

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT category, ROUND(SUM(amount), 2) AS total
            FROM expenses
            WHERE user_id = ? AND DATE(created_at) >= ?
            GROUP BY category
            ORDER BY total DESC
            """,
            (user_id, month_start),
        ).fetchall()
        # Fallback: if no current-month data, use last 60 days so AI always has context
        if not rows:
            cutoff = (today - timedelta(days=60)).isoformat()
            rows = conn.execute(
                """
                SELECT category, ROUND(SUM(amount), 2) AS total
                FROM expenses
                WHERE user_id = ? AND DATE(created_at) >= ?
                GROUP BY category
                ORDER BY total DESC
                """,
                (user_id, cutoff),
            ).fetchall()
        by_category = [(r["category"], float(r["total"])) for r in rows]

        month_spent = float(
            conn.execute(
                "SELECT COALESCE(ROUND(SUM(amount), 2), 0) FROM expenses WHERE user_id = ? AND DATE(created_at) >= ?",
                (user_id, month_start),
            ).fetchone()[0]
        )

        profile = conn.execute(
            "SELECT monthly_income, goal_text FROM profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        unplanned = float(
            conn.execute(
                "SELECT COALESCE(ROUND(SUM(amount), 2), 0) FROM incomes WHERE user_id = ? AND DATE(created_at) >= ?",
                (user_id, month_start),
            ).fetchone()[0]
        )

        budget_rows = conn.execute(
            "SELECT category, monthly_limit FROM budgets WHERE user_id = ? ORDER BY category",
            (user_id,),
        ).fetchall()
        budgets = [(r["category"], float(r["monthly_limit"])) for r in budget_rows]

        daily_rows = conn.execute(
            """
            SELECT DATE(created_at) AS day, ROUND(SUM(amount), 2) AS total
            FROM expenses
            WHERE user_id = ? AND DATE(created_at) >= ?
            GROUP BY DATE(created_at)
            ORDER BY day
            """,
            (user_id, month_start),
        ).fetchall()

    # Build complete day-by-day series (fill gaps with 0)
    day_map = {r["day"]: float(r["total"]) for r in daily_rows}
    d = today.replace(day=1)
    daily_spending = []
    while d <= today:
        daily_spending.append([d.isoformat(), day_map.get(d.isoformat(), 0.0)])
        d += timedelta(days=1)

    monthly_income = (
        float(profile["monthly_income"])
        if profile and profile["monthly_income"] is not None
        else None
    )
    goal_text = profile["goal_text"] if profile else None

    projected_eom = (month_spent / days_passed * dim) if days_passed else month_spent
    total_income = (monthly_income or 0.0) + unplanned
    projected_balance = (
        round(total_income - projected_eom, 2) if monthly_income is not None else None
    )

    cat_map = dict(by_category)
    overspent = [
        (cat, cat_map.get(cat, 0.0), lim)
        for cat, lim in budgets
        if cat_map.get(cat, 0.0) > lim
    ]

    return {
        "today": today.isoformat(),
        "month_name": today.strftime("%B %Y"),
        "days_passed": days_passed,
        "days_in_month": dim,
        "month_spent": month_spent,
        "projected_eom_spent": round(projected_eom, 2),
        "projected_balance": projected_balance,
        "monthly_income": monthly_income,
        "month_unplanned_income": unplanned,
        "goal_text": goal_text,
        "by_category": by_category,
        "budgets": budgets,
        "overspent": overspent,
        "daily_spending": daily_spending,
    }
