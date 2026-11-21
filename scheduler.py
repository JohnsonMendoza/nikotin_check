"""
scheduler.py — ежедневные утренние уведомления (APScheduler) и разовые таймеры.

Два способа запуска рассылки:
  1) внутренний планировщик: задание каждый час в :00 смотрит, у кого этот час
     выбран для утреннего сообщения, и отправляет (локальный запуск, VPS);
  2) внешний планировщик: GET /cron/<CRON_KEY> — для бесплатного хостинга,
     где процесс может «засыпать» (Render) или недоступен фоновый процесс
     (PythonAnywhere: там есть бесплатная ежедневная задача по расписанию).

Повторная отправка за один день невозможна: поле last_notify_day.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

import config
import content
import db
import keyboards as kb
import utils

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def init_scheduler() -> AsyncIOScheduler:
    """Создаёт (один раз) планировщик в часовом поясе проекта."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            timezone=config.TIMEZONE,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
        )
    return _scheduler


def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = init_scheduler()
    scheduler.add_job(
        send_daily_notifications,
        trigger=CronTrigger(minute=0, timezone=config.TIMEZONE),  # каждый час в :00
        args=[bot],
        id="daily_notifications",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()
        log.info("Планировщик запущен: рассылка проверяется каждый час (%s)", config.TIMEZONE)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


async def send_daily_notifications(bot: Bot, hour: int | None = None) -> int:
    """Отправляет утренние сообщения тем, у кого выбран этот час. Возвращает число отправок."""
    now = utils.now_local()
    hour = now.hour if hour is None else int(hour)
    today = utils.today_local().isoformat()
    users = await db.users_for_notification(hour, today)
    sent = 0

    for user in users:
        user_id = int(user["user_id"])
        clean = await db.count_clean_days(user_id)
        text = utils.render_daily(user, clean)
        try:
            await bot.send_message(user_id, text, reply_markup=kb.main_menu())
            await db.update_user(user_id, last_notify_day=today)
            sent += 1

            # Приглашение к анкете — на 7-й и 30-й день, один раз (пока анкета не заполнена).
            days = utils.days_since(user["quit_date"])
            if days in (7, 30) and not user["feedback_done"]:
                await bot.send_message(user_id, content.FEEDBACK_INVITE, reply_markup=kb.feedback_invite_kb())

            await asyncio.sleep(0.05)  # бережём лимиты Telegram (~30 сообщений в секунду)

        except TelegramForbiddenError:
            # Пользователь заблокировал бота или удалил чат — больше не пишем.
            await db.update_user(user_id, notify_enabled=0)
            log.info("Пользователь недоступен, уведомления отключены (id скрыт в логах)")
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await bot.send_message(user_id, text, reply_markup=kb.main_menu())
                await db.update_user(user_id, last_notify_day=today)
                sent += 1
            except TelegramAPIError:
                pass
        except TelegramAPIError as exc:
            log.warning("Не удалось отправить уведомление: %s", type(exc).__name__)

    if sent:
        log.info("Утренняя рассылка: отправлено %s сообщений (час %s)", sent, hour)
    return sent


def schedule_delayed_message(bot: Bot, chat_id: int, text: str, delay_seconds: int) -> bool:
    """Разовое сообщение через delay_seconds (правило «отложить на 10 минут»)."""
    scheduler = _scheduler
    if scheduler is None or not scheduler.running:
        return False
    run_date = dt.datetime.now(utils.TZ) + dt.timedelta(seconds=delay_seconds)
    scheduler.add_job(
        _send_once,
        trigger=DateTrigger(run_date=run_date, timezone=utils.TZ),
        args=[bot, chat_id, text],
        id=f"delayed:{chat_id}",
        replace_existing=True,
    )
    return True


async def _send_once(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=kb.sos_intro_kb())
    except TelegramAPIError as exc:
        log.warning("Разовое сообщение не доставлено: %s", type(exc).__name__)
