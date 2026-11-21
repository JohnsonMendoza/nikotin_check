"""
handlers/onboarding.py — /start, согласие на хранение данных и три вопроса настройки.

Сценарий (FSM — конечный автомат состояний aiogram):
  /start → согласие → режим → дата отказа → расход в день → время уведомлений → готово
Шаги «дата», «сумма», «час» можно пройти кнопкой или текстом; текст ловится состоянием.
"""

from __future__ import annotations

import datetime as dt

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import config
import content
import db
import keyboards as kb
import utils
from handlers.common import make_router, safe_edit

router = make_router("onboarding")


class Onboarding(StatesGroup):
    """Состояния, в которых бот ждёт от пользователя текстовый ответ."""
    quit_date = State()
    cost = State()
    notify_hour = State()


# --------------------------------------------------------------- /start -----

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    user = await db.get_user(user_id)

    # Пользователь уже настроен — не мучаем онбордингом повторно.
    if user and user["consent"] and user["quit_date"]:
        clean = await db.count_clean_days(user_id)
        await message.answer(
            content.WELCOME_BACK.format(summary=utils.short_summary(user, clean)),
            reply_markup=kb.main_menu(),
        )
        return

    await message.answer(content.WELCOME)
    await message.answer(
        content.ANONYMITY + "\n\n" + content.CONSENT_ASK,
        reply_markup=kb.consent_kb(),
    )


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.answer(content.ABOUT_CMD)


# ------------------------------------------------------------- согласие -----

@router.callback_query(kb.OnboardCB.filter(F.step == "consent"))
async def on_consent(cb: CallbackQuery, callback_data: kb.OnboardCB, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    value = callback_data.value

    if value == "about":
        await bot.send_message(cb.from_user.id, content.PROJECT_ABOUT)
        return

    if value == "no":
        # Никакой записи в базу: человек не дал согласия.
        await safe_edit(cb, bot, content.CONSENT_DECLINED)
        return

    await db.ensure_user(cb.from_user.id, consent=1)
    await state.clear()
    await safe_edit(cb, bot, content.ONBOARD_MODE_ASK, kb.mode_kb())


# ----------------------------------------------------------------- режим ----

@router.callback_query(kb.OnboardCB.filter(F.step == "mode"))
async def on_mode(cb: CallbackQuery, callback_data: kb.OnboardCB, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    user_id = cb.from_user.id
    await state.clear()

    if callback_data.value == "watch":
        today = utils.today_local().isoformat()
        await db.update_user(user_id, mode="watch", quit_date=today)
        await safe_edit(cb, bot, content.ONBOARD_WATCH_OK)
        await bot.send_message(user_id, content.ONBOARD_COST_ASK, reply_markup=kb.cost_kb())
        return

    await db.update_user(user_id, mode="quit")
    await safe_edit(cb, bot, content.ONBOARD_DATE_ASK, kb.date_kb())


# ------------------------------------------------------------------ дата ----

@router.callback_query(kb.OnboardCB.filter(F.step == "date"))
async def on_date_button(cb: CallbackQuery, callback_data: kb.OnboardCB, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    if callback_data.value == "manual":
        await state.set_state(Onboarding.quit_date)
        await safe_edit(cb, bot, content.ONBOARD_DATE_MANUAL)
        return

    today = utils.today_local()
    day = today if callback_data.value == "today" else today - dt.timedelta(days=1)
    await state.clear()
    await db.update_user(cb.from_user.id, quit_date=day.isoformat())
    await safe_edit(cb, bot, content.ONBOARD_COST_ASK, kb.cost_kb())


@router.message(StateFilter(Onboarding.quit_date), F.text)
async def on_date_text(message: Message, state: FSMContext) -> None:
    day = utils.parse_user_date(message.text or "")
    if day is None:
        await message.answer(content.ERR_DATE)
        return
    await state.clear()
    await db.update_user(message.from_user.id, quit_date=day.isoformat())
    await message.answer(content.ONBOARD_COST_ASK, reply_markup=kb.cost_kb())


# ---------------------------------------------------------------- расход ----

@router.callback_query(kb.OnboardCB.filter(F.step == "cost"))
async def on_cost_button(cb: CallbackQuery, callback_data: kb.OnboardCB, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    if callback_data.value == "manual":
        await state.set_state(Onboarding.cost)
        await safe_edit(cb, bot, content.ONBOARD_COST_MANUAL)
        return
    await state.clear()
    await db.update_user(cb.from_user.id, daily_cost=float(callback_data.value))
    await safe_edit(cb, bot, content.ONBOARD_NOTIFY_ASK, kb.notify_kb(config.DEFAULT_NOTIFY_HOUR))


@router.message(StateFilter(Onboarding.cost), F.text)
async def on_cost_text(message: Message, state: FSMContext) -> None:
    value = _parse_cost(message.text or "")
    if value is None:
        await message.answer(content.ERR_COST)
        return
    await state.clear()
    await db.update_user(message.from_user.id, daily_cost=value)
    await message.answer(content.ONBOARD_NOTIFY_ASK, reply_markup=kb.notify_kb(config.DEFAULT_NOTIFY_HOUR))


def _parse_cost(text: str) -> float | None:
    """'250', '250 руб', '250,5' → 250.0. Всё остальное → None."""
    raw = text.strip().lower().replace(",", ".").replace("₽", "").replace("руб", "").replace("р.", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    if not 0 <= value <= 20000:
        return None
    return round(value, 2)


# --------------------------------------------------------- уведомления ------

@router.callback_query(kb.OnboardCB.filter(F.step == "notify"))
async def on_notify_button(cb: CallbackQuery, callback_data: kb.OnboardCB, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    value = callback_data.value
    if value == "manual":
        await state.set_state(Onboarding.notify_hour)
        await safe_edit(cb, bot, content.SETTINGS_HOUR_ASK)
        return

    await state.clear()
    if value == "off":
        await db.update_user(cb.from_user.id, notify_enabled=0)
    else:
        await db.update_user(cb.from_user.id, notify_enabled=1, notify_hour=int(value))
    await _finish(cb.from_user.id, bot)


@router.message(StateFilter(Onboarding.notify_hour), F.text)
async def on_notify_text(message: Message, state: FSMContext) -> None:
    hour = _parse_hour(message.text or "")
    if hour is None:
        await message.answer(content.ERR_HOUR)
        return
    await state.clear()
    await db.update_user(message.from_user.id, notify_enabled=1, notify_hour=hour)
    await _finish(message.from_user.id, message.bot)


def _parse_hour(text: str) -> int | None:
    raw = text.strip().split(":")[0]
    try:
        hour = int(raw)
    except ValueError:
        return None
    return hour if 0 <= hour <= 23 else None


async def _finish(user_id: int, bot: Bot) -> None:
    """Финал онбординга: короткая сводка + нижнее меню."""
    user = await db.get_user(user_id)
    if not user:
        return
    clean = await db.count_clean_days(user_id)
    await bot.send_message(
        user_id,
        content.ONBOARD_DONE.format(summary=utils.short_summary(user, clean)),
        reply_markup=kb.main_menu(),
    )
