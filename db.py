"""
db.py — слой хранения данных (SQLite через aiosqlite).

Принципы анонимности (требование заказчика):
  * единственный идентификатор — числовой user_id из Telegram (псевдоним);
  * НЕ хранятся: имя, фамилия, username, телефон, тексты сообщений, IP;
  * согласие фиксируется отдельным полем и требуется до первой записи данных;
  * /delete физически удаляет все строки пользователя из всех таблиц.

Таблицы:
  users      — профиль и настройки (1 строка на пользователя)
  clean_days — отметки «сегодня без никотина» для режима наблюдения
  relapses   — обезличенные триггеры срывов (для социологического анализа)
  feedback   — ответы мини-анкеты (3 вопроса)
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,      -- псевдоним из Telegram, других ID нет
    consent         INTEGER NOT NULL DEFAULT 0,
    mode            TEXT    NOT NULL DEFAULT 'quit',   -- quit | watch
    quit_date       TEXT,                     -- ISO YYYY-MM-DD, первый день без никотина
    daily_cost      REAL    NOT NULL DEFAULT 0,        -- расход в день, ₽
    notify_enabled  INTEGER NOT NULL DEFAULT 1,
    notify_hour     INTEGER NOT NULL DEFAULT 9,
    relapse_count   INTEGER NOT NULL DEFAULT 0,
    last_notify_day TEXT,                     -- защита от повторной рассылки за день
    feedback_done   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS clean_days (
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS relapses (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,
    trigger TEXT                              -- stress | company | boredom | alcohol | habit | other
);

CREATE TABLE IF NOT EXISTS feedback (
    user_id    INTEGER PRIMARY KEY,           -- одна анкета на пользователя (перезаписывается)
    q1         INTEGER,                       -- 1..5, насколько помогает
    q2         TEXT,                          -- что полезнее всего
    q3         TEXT,                          -- свободный текст (может быть пустым)
    created_at TEXT NOT NULL
);
"""


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    """Новое соединение на операцию: просто, надёжно и достаточно для сотен пользователей."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row  # строки как словари: row["quit_date"]
        yield conn


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Вызывается при старте бота."""
    async with _connect() as db:
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")  # устойчивее к перезапускам
        await db.commit()


# ---------------------------------------------------------------- профиль ---

async def get_user(user_id: int) -> dict[str, Any] | None:
    async with _connect() as db:
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def ensure_user(user_id: int, consent: int = 1) -> dict[str, Any]:
    """Создаёт профиль при данном согласии. Повторный /start данные не стирает."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    async with _connect() as db:
        await db.execute(
            """INSERT INTO users (user_id, consent, notify_hour, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET consent = excluded.consent""",
            (user_id, consent, config.DEFAULT_NOTIFY_HOUR, now),
        )
        await db.commit()
    user = await get_user(user_id)
    assert user is not None
    return user


async def update_user(user_id: int, **fields: Any) -> None:
    """Обновляет только разрешённые поля (защита от SQL-инъекций через имена колонок)."""
    allowed = {
        "consent", "mode", "quit_date", "daily_cost",
        "notify_enabled", "notify_hour", "relapse_count",
        "last_notify_day", "feedback_done",
    }
    pairs = [(k, v) for k, v in fields.items() if k in allowed]
    if not pairs:
        return
    assignments = ", ".join(f"{k} = ?" for k, _ in pairs)
    values = [v for _, v in pairs] + [user_id]
    async with _connect() as db:
        await db.execute(f"UPDATE users SET {assignments} WHERE user_id = ?", values)
        await db.commit()


async def delete_user(user_id: int) -> None:
    """Полное удаление данных пользователя (/delete)."""
    async with _connect() as db:
        for table in ("users", "clean_days", "relapses", "feedback"):
            await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        await db.commit()


# ------------------------------------------------------- отметки и срывы ---

async def mark_clean_day(user_id: int, day: str) -> bool:
    """True — день отмечен впервые, False — уже был отмечен."""
    async with _connect() as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO clean_days (user_id, day) VALUES (?, ?)", (user_id, day)
        )
        await db.commit()
        return cur.rowcount > 0


async def count_clean_days(user_id: int) -> int:
    async with _connect() as db:
        cur = await db.execute("SELECT COUNT(*) AS n FROM clean_days WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return int(row["n"]) if row else 0


async def add_relapse(user_id: int, day: str, trigger: str | None) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO relapses (user_id, day, trigger) VALUES (?, ?, ?)", (user_id, day, trigger)
        )
        await db.commit()


async def set_last_relapse_trigger(user_id: int, trigger: str) -> None:
    """Проставляет триггер последнему срыву пользователя."""
    async with _connect() as db:
        await db.execute(
            """UPDATE relapses SET trigger = ?
               WHERE id = (SELECT id FROM relapses WHERE user_id = ? ORDER BY id DESC LIMIT 1)""",
            (trigger, user_id),
        )
        await db.commit()


# ------------------------------------------------------------- анкета ------

async def save_feedback(user_id: int, q1: int | None, q2: str | None, q3: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    async with _connect() as db:
        await db.execute(
            """INSERT INTO feedback (user_id, q1, q2, q3, created_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 q1 = excluded.q1, q2 = excluded.q2, q3 = excluded.q3, created_at = excluded.created_at""",
            (user_id, q1, q2, q3, now),
        )
        await db.commit()
    await update_user(user_id, feedback_done=1)


# ------------------------------------------------- выборки для рассылки -----

async def users_for_notification(hour: int, today: str) -> list[dict[str, Any]]:
    """Кому слать утреннее сообщение прямо сейчас: согласие + включено + этот час + ещё не слали сегодня."""
    async with _connect() as db:
        cur = await db.execute(
            """SELECT * FROM users
               WHERE consent = 1 AND notify_enabled = 1 AND notify_hour = ?
                 AND quit_date IS NOT NULL
                 AND (last_notify_day IS NULL OR last_notify_day != ?)""",
            (hour, today),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def all_stats() -> dict[str, Any]:
    """Агрегаты для отчёта по курсовому. Никаких user_id в выдаче."""
    async with _connect() as db:
        async def scalar(sql: str, params: tuple = ()) -> Any:
            cur = await db.execute(sql, params)
            row = await cur.fetchone()
            return row[0] if row else None

        users = await scalar("SELECT COUNT(*) FROM users") or 0
        consented = await scalar("SELECT COUNT(*) FROM users WHERE consent = 1") or 0
        quit_mode = await scalar("SELECT COUNT(*) FROM users WHERE consent = 1 AND mode = 'quit'") or 0
        watch_mode = await scalar("SELECT COUNT(*) FROM users WHERE consent = 1 AND mode = 'watch'") or 0
        notify_on = await scalar("SELECT COUNT(*) FROM users WHERE consent = 1 AND notify_enabled = 1") or 0
        relapses = await scalar("SELECT COUNT(*) FROM relapses") or 0
        clean_days = await scalar("SELECT COUNT(*) FROM clean_days") or 0

        cur = await db.execute("SELECT quit_date FROM users WHERE consent = 1 AND quit_date IS NOT NULL")
        dates = [r["quit_date"] for r in await cur.fetchall()]

        cur = await db.execute("SELECT q1, q2 FROM feedback")
        fb = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute("SELECT trigger, COUNT(*) AS n FROM relapses GROUP BY trigger ORDER BY n DESC")
        triggers = [(r["trigger"] or "не указан", r["n"]) for r in await cur.fetchall()]

    today = dt.date.today()
    days: list[int] = []
    for iso in dates:
        try:
            days.append(max(0, (today - dt.date.fromisoformat(iso)).days))
        except ValueError:
            continue
    days.sort()

    def median(xs: list[int]) -> float:
        if not xs:
            return 0.0
        mid = len(xs) // 2
        return float(xs[mid]) if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2

    q1_values = [r["q1"] for r in fb if r["q1"] is not None]
    q2_counts: dict[str, int] = {}
    for r in fb:
        if r["q2"]:
            q2_counts[r["q2"]] = q2_counts.get(r["q2"], 0) + 1

    return {
        "users": users,
        "consented": consented,
        "quit_mode": quit_mode,
        "watch_mode": watch_mode,
        "notify_on": notify_on,
        "relapses": relapses,
        "clean_days": clean_days,
        "avg_days": round(sum(days) / len(days), 1) if days else 0.0,
        "median_days": median(days),
        "max_days": max(days) if days else 0,
        "feedback_n": len(fb),
        "q1_avg": round(sum(q1_values) / len(q1_values), 2) if q1_values else 0.0,
        "q2_counts": sorted(q2_counts.items(), key=lambda kv: -kv[1]),
        "triggers": triggers,
    }


async def export_feedback_csv(path: str | Path) -> int:
    """Выгружает обезличенные ответы анкеты в CSV (без user_id) — для приложения к курсовому."""
    import csv

    async with _connect() as db:
        cur = await db.execute("SELECT q1, q2, q3, created_at FROM feedback ORDER BY created_at")
        rows = [dict(r) for r in await cur.fetchall()]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["q1", "q2", "q3", "created_at"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def db_exists() -> bool:
    return Path(config.DB_PATH).exists()
