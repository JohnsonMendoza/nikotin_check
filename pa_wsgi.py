"""
pa_wsgi.py — запуск бота на PythonAnywhere (бесплатный аккаунт) через webhook.

Зачем отдельный файл: на PythonAnywhere веб-приложение обслуживается WSGI-сервером
(uWSGI), а не aiohttp, и «вечные» фоновые процессы на бесплатном тарифе недоступны.
Поэтому:
  * Flask принимает POST от Telegram по адресу WEBHOOK_SECRET_PATH;
  * отдельный поток держит asyncio-цикл, в нём живут aiogram и планировщик;
  * обработчик отвечает Telegram сразу, не дожидаясь конца длинного сценария (SOS идёт ~2 минуты);
  * утреннюю рассылку запускает бесплатная «Scheduled task» PythonAnywhere,
    обращаясь к адресу /cron/<CRON_KEY> (см. README).

В настройках веб-приложения PythonAnywhere укажи:
    source code:  /home/<username>/nicotine_free_bot/pa_wsgi.py
    WSGI config:  from pa_wsgi import application
"""

from __future__ import annotations

import asyncio
import logging
import threading

from aiogram.types import Update
from flask import Flask, request

import config
import db
import scheduler
from bot import create_bot, create_dispatcher

config.validate()
log = logging.getLogger("pa_wsgi")

# --- фоновый asyncio-цикл ----------------------------------------------------
_loop = asyncio.new_event_loop()
_bot = create_bot()
_dp = create_dispatcher()


def _run_loop() -> None:
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


threading.Thread(target=_run_loop, daemon=True, name="aiogram-loop").start()


async def _setup() -> None:
    await db.init_db()
    await _dp.emit_startup(bot=_bot, dispatcher=_dp)


asyncio.run_coroutine_threadsafe(_setup(), _loop).result(timeout=60)

application = Flask(__name__)


@application.get("/")
@application.get("/health")
def health() -> tuple[str, int]:
    return "ok", 200


@application.post(config.WEBHOOK_SECRET_PATH)
def telegram_webhook() -> tuple[str, int]:
    payload = request.get_json(force=True, silent=True)
    if not payload:
        return "bad request", 400
    update = Update.model_validate(payload, context={"bot": _bot})
    # Не ждём завершения: Telegram должен получить 200 быстро.
    future = asyncio.run_coroutine_threadsafe(_dp.feed_update(_bot, update), _loop)
    future.add_done_callback(_log_error)
    return "ok", 200


@application.get("/cron/<key>")
def cron(key: str) -> tuple[str, int]:
    if not config.CRON_KEY or key != config.CRON_KEY:
        return "forbidden", 403
    hour_arg = request.args.get("hour")
    hour = int(hour_arg) if hour_arg and hour_arg.isdigit() else None
    future = asyncio.run_coroutine_threadsafe(scheduler.send_daily_notifications(_bot, hour), _loop)
    sent = future.result(timeout=120)
    return f"sent={sent}", 200


def _log_error(future) -> None:
    exc = future.exception()
    if exc:
        log.error("Ошибка обработки апдейта: %s", exc, exc_info=exc)


if __name__ == "__main__":
    # Локальная отладка адаптера (на самом PythonAnywhere запускается uWSGI).
    application.run(host="0.0.0.0", port=config.PORT)
