"""
config.py — единая точка чтения настроек из переменных окружения / файла .env.

Правило проекта: в остальных модулях нет os.getenv — только `import config`.
Это упрощает перенос на хостинг: меняются переменные окружения, а не код.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# .env лежит рядом с кодом; на хостинге переменные задаются в панели,
# и тогда load_dotenv просто ничего не перезаписывает.
load_dotenv(BASE_DIR / ".env")


def _get_int(name: str, default: int) -> int:
    """Аккуратно читает целое число: пустая строка или мусор → значение по умолчанию."""
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip().replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return default


# --- обязательное ------------------------------------------------------------
BOT_TOKEN: str = (os.getenv("BOT_TOKEN") or "").strip()

# --- данные ------------------------------------------------------------------
_db_raw = (os.getenv("DB_PATH") or "data/bot.sqlite3").strip()
DB_PATH: Path = Path(_db_raw)
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

# --- время -------------------------------------------------------------------
TIMEZONE: str = (os.getenv("TIMEZONE") or "Europe/Moscow").strip()
DEFAULT_NOTIFY_HOUR: int = min(23, max(0, _get_int("DEFAULT_NOTIFY_HOUR", 9)))

# --- режим запуска -----------------------------------------------------------
MODE: str = (os.getenv("MODE") or "polling").strip().lower()  # polling | webhook
PORT: int = _get_int("PORT", 8080)
WEBHOOK_BASE_URL: str = (os.getenv("WEBHOOK_BASE_URL") or "").strip().rstrip("/")
PROXY_URL: str = (os.getenv("PROXY_URL") or "").strip()

# Секретный путь webhook: не даёт посторонним слать боту поддельные апдейты.
# Детерминированно выводится из токена, отдельно хранить не нужно.
WEBHOOK_SECRET_PATH: str = "/webhook/" + hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:24]
WEBHOOK_URL: str = WEBHOOK_BASE_URL + WEBHOOK_SECRET_PATH

# Ключ для внешнего планировщика: GET /cron/<CRON_KEY> запускает утреннюю рассылку.
CRON_KEY: str = (os.getenv("CRON_KEY") or "").strip()

# --- служебное ---------------------------------------------------------------
ADMIN_ID: int | None = _get_int("ADMIN_ID", 0) or None
# 1.0 — реальная длительность практики 4-7-8; 0.05 — быстрый прогон для демо/тестов.
SOS_TIME_SCALE: float = max(0.01, _get_float("SOS_TIME_SCALE", 1.0))


def validate() -> None:
    """Понятная ошибка вместо стектрейса, если новичок забыл настроить .env."""
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        raise SystemExit(
            "\n[ОШИБКА] Не задан BOT_TOKEN.\n"
            "1) Скопируй .env.example в файл .env\n"
            "2) Вставь токен от @BotFather в строку BOT_TOKEN=...\n"
            "3) Запусти снова: python bot.py\n"
        )
    if MODE not in ("polling", "webhook"):
        raise SystemExit(f"[ОШИБКА] MODE должен быть polling или webhook, а не {MODE!r}")
    if MODE == "webhook" and not WEBHOOK_BASE_URL.startswith("https://"):
        raise SystemExit(
            "[ОШИБКА] Для MODE=webhook нужен WEBHOOK_BASE_URL=https://... "
            "(публичный адрес твоего сервиса на хостинге).\n"
            "Для локального запуска поставь MODE=polling."
        )
