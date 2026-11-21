"""
handlers/core.py — трекер, срыв, настройки, живая помощь, админ-статистика.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

import config
import content
import db
import keyboards as kb
import utils
from handlers.common import make_router, require_profile, safe_edit

router = make_router("core")


class Settings(StatesGroup):
    cost = State()
    date = State()
    hour = State()


# ------------------------------------------------------------- /progress ----

@router.message(Command("progress"))
async def show_progress(message: Message) -> None:
    user_id = message.from_user.id
    user = await require_profile(user_id, message.bot, message.chat.id)
    if not user:
        return
    clean = await db.count_clean_days(user_id)
    await message.answer(utils.render_progress(user, clean), reply_markup=kb.progress_kb(user["mode"]))


# ---------------------------------------------- отметка дня (режим watch) ---

async def do_checkin(user_id: int, bot: Bot, chat_id: int) -> None:
    user = await require_profile(user_id, bot, chat_id)
    if not user:
        return
    if user["mode"] != "watch":
        # В режиме отказа дни считаются автоматически — отметки не нужны.
        await bot.send_message(chat_id, content.CHECKIN_QUIT_MODE)
        return
    day = utils.today_local().isoformat()
    is_new = await db.mark_clean_day(user_id, day)
    clean = await db.count_clean_days(user_id)
    money = utils.fmt_money(utils.money_saved(clean, user["daily_cost"]))
    template = content.CHECKIN_OK if is_new else content.CHECKIN_DUPLICATE
    await bot.send_message(
        chat_id,
        template.format(date=utils.fmt_date(day), clean=clean, money=money),
        reply_markup=kb.progress_kb(user["mode"]),
    )


@router.callback_query(kb.SettingsCB.filter(F.action == "checkin"))
async def cb_checkin(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer("Отмечаю ✅")
    await do_checkin(cb.from_user.id, bot, cb.from_user.id)


# ----------------------------------------------------------------- /reset ---

@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    user = await require_profile(message.from_user.id, message.bot, message.chat.id)
    if not user:
        return
    await message.answer(content.RESET_ASK, reply_markup=kb.reset_confirm_kb())


@router.callback_query(kb.ResetCB.filter(F.action == "ask"))
async def cb_reset_ask(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    await safe_edit(cb, bot, content.RESET_ASK, kb.reset_confirm_kb())


@router.callback_query(kb.ResetCB.filter(F.action == "cancel"))
async def cb_reset_cancel(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    await safe_edit(cb, bot, content.RESET_CANCELLED)


@router.callback_query(kb.ResetCB.filter(F.action == "confirm"))
async def cb_reset_confirm(cb: CallbackQuery, bot: Bot) -> None:
    """Сброс счётчика. Ключевое требование: поддерживающий тон, без осуждения."""
    await cb.answer()
    user_id = cb.from_user.id
    user = await require_profile(user_id, bot, user_id)
    if not user:
        return

    prev_days = utils.days_since(user["quit_date"])
    today = utils.today_local().isoformat()
    await db.update_user(
        user_id,
        quit_date=today,
        mode="quit",
        relapse_count=int(user["relapse_count"] or 0) + 1,
    )
    await db.add_relapse(user_id, today, None)  # триггер уточним следующим сообщением
    # Если счётчик уже стартовал сегодня, не пишем «опыт предыдущих 0 дней».
    text = (
        content.RESET_DONE.format(
            date=utils.fmt_date(today),
            prev_days=prev_days,
            prev_days_word=utils.days_word(prev_days),
        )
        if prev_days > 0
        else content.RESET_DONE_SAME_DAY
    )
    await safe_edit(cb, bot, text, kb.triggers_kb())


@router.callback_query(kb.ResetCB.filter(F.action == "trigger"))
async def cb_reset_trigger(cb: CallbackQuery, callback_data: kb.ResetCB, bot: Bot) -> None:
    await cb.answer()
    labels = dict(content.RELAPSE_TRIGGERS)
    trigger = callback_data.value
    await db.set_last_relapse_trigger(cb.from_user.id, trigger)
    await safe_edit(cb, bot, content.RESET_TRIGGER_THANKS.format(trigger=labels.get(trigger, trigger)))


# ------------------------------------------------------------------ /help ---

@router.message(Command("help"))
async def show_help(message: Message) -> None:
    await message.answer(utils.render_help())


# -------------------------------------------------------------- /settings ---

@router.message(Command("settings"))
async def show_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = await require_profile(message.from_user.id, message.bot, message.chat.id)
    if not user:
        return
    await message.answer(utils.render_settings(user), reply_markup=kb.settings_kb(bool(user["notify_enabled"]), user["mode"]))


@router.callback_query(kb.SettingsCB.filter(F.action == "cost"))
async def cb_set_cost(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Settings.cost)
    await safe_edit(cb, bot, content.SETTINGS_COST_ASK)


@router.message(StateFilter(Settings.cost), F.text)
async def on_settings_cost(message: Message, state: FSMContext) -> None:
    from handlers.onboarding import _parse_cost  # локальный импорт: избегаем цикла модулей

    value = _parse_cost(message.text or "")
    if value is None:
        await message.answer(content.ERR_COST)
        return
    await state.clear()
    await db.update_user(message.from_user.id, daily_cost=value)
    await message.answer(content.SETTINGS_COST_OK.format(cost=utils.fmt_money(value)))
    await show_settings(message, state)


@router.callback_query(kb.SettingsCB.filter(F.action == "date"))
async def cb_set_date(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Settings.date)
    await safe_edit(cb, bot, content.ONBOARD_DATE_MANUAL)


@router.message(StateFilter(Settings.date), F.text)
async def on_settings_date(message: Message, state: FSMContext) -> None:
    day = utils.parse_user_date(message.text or "")
    if day is None:
        await message.answer(content.ERR_DATE)
        return
    await state.clear()
    await db.update_user(message.from_user.id, quit_date=day.isoformat())
    await message.answer(content.SETTINGS_DATE_OK.format(date=utils.fmt_date(day.isoformat())))
    await show_settings(message, state)


@router.callback_query(kb.SettingsCB.filter(F.action == "hour"))
async def cb_set_hour(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Settings.hour)
    await safe_edit(cb, bot, content.SETTINGS_HOUR_ASK)


@router.message(StateFilter(Settings.hour), F.text)
async def on_settings_hour(message: Message, state: FSMContext) -> None:
    from handlers.onboarding import _parse_hour

    hour = _parse_hour(message.text or "")
    if hour is None:
        await message.answer(content.ERR_HOUR)
        return
    await state.clear()
    await db.update_user(message.from_user.id, notify_enabled=1, notify_hour=hour)
    await message.answer(content.SETTINGS_HOUR_OK.format(hour=hour))
    await show_settings(message, state)


@router.callback_query(kb.SettingsCB.filter(F.action == "toggle"))
async def cb_toggle_notify(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    user = await require_profile(cb.from_user.id, bot, cb.from_user.id)
    if not user:
        return
    new_state = 0 if user["notify_enabled"] else 1
    await db.update_user(cb.from_user.id, notify_enabled=new_state)
    label = content.SETTINGS_NOTIFY_ON.format(hour=user["notify_hour"]) if new_state else content.SETTINGS_NOTIFY_OFF
    await bot.send_message(cb.from_user.id, content.SETTINGS_NOTIFY_TOGGLED.format(state=label))
    user = await db.get_user(cb.from_user.id)
    await safe_edit(cb, bot, utils.render_settings(user), kb.settings_kb(bool(new_state), user["mode"]))


@router.callback_query(kb.SettingsCB.filter(F.action == "mode"))
async def cb_switch_mode(cb: CallbackQuery, callback_data: kb.SettingsCB, bot: Bot) -> None:
    """Переключение quit ↔ watch. Дата отсчёта начинается с сегодня."""
    await cb.answer()
    user_id = cb.from_user.id
    user = await require_profile(user_id, bot, user_id)
    if not user:
        return
    today = utils.today_local().isoformat()
    target = callback_data.value or ("watch" if user["mode"] == "quit" else "quit")
    await db.update_user(user_id, mode=target, quit_date=today)
    if target == "quit":
        await bot.send_message(user_id, content.SWITCH_TO_QUIT.format(date=utils.fmt_date(today)))
    else:
        await bot.send_message(user_id, content.ONBOARD_WATCH_OK)
    user = await db.get_user(user_id)
    await bot.send_message(user_id, utils.render_settings(user), reply_markup=kb.settings_kb(bool(user["notify_enabled"]), user["mode"]))


# --------------------------------------------------------------- /delete ----

@router.message(Command("delete"))
async def cmd_delete(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(content.DELETE_ASK, reply_markup=kb.delete_confirm_kb())


@router.callback_query(kb.SettingsCB.filter(F.action == "delete"))
async def cb_delete(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    await safe_edit(cb, bot, content.DELETE_ASK, kb.delete_confirm_kb())


@router.callback_query(kb.SettingsCB.filter(F.action == "cancel_delete"))
async def cb_delete_cancel(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    await safe_edit(cb, bot, content.DELETE_CANCELLED)


@router.callback_query(kb.SettingsCB.filter(F.action == "confirm_delete"))
async def cb_delete_confirm(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await db.delete_user(cb.from_user.id)
    await safe_edit(cb, bot, content.DELETE_DONE)


# --------------------------------------------------- админ: /stats, /export --

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Только агрегаты для отчёта по курсовому. Персональных срезов нет вообще."""
    if config.ADMIN_ID is None or message.from_user.id != config.ADMIN_ID:
        await message.answer(content.ADMIN_ONLY)
        return
    stats = await db.all_stats()
    await message.answer(utils.render_admin_stats(stats))


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Выгрузка обезличенных анкет в CSV (без user_id)."""
    if config.ADMIN_ID is None or message.from_user.id != config.ADMIN_ID:
        await message.answer(content.ADMIN_ONLY)
        return
    path = config.DB_PATH.parent / "feedback_anonymous.csv"
    rows = await db.export_feedback_csv(path)
    if not rows:
        await message.answer(content.EXPORT_EMPTY)
        return
    await message.answer_document(FSInputFile(path), caption=content.EXPORT_CAPTION)
