"""
handlers/sos.py — экстренная помощь при тяге: ведущая практика 4-7-8 и приёмы отвлечения.

Как это работает технически:
  * бот редактирует одно и то же сообщение, меняя фазу дыхания (вдох 4 → задержка 7 → выдох 8);
  * паузы делаются через asyncio, поэтому бот в это время обслуживает остальных пользователей;
  * кнопка «Стоп» поднимает asyncio.Event, и практика прерывается мгновенно;
  * config.SOS_TIME_SCALE ускоряет практику для демонстрации на защите (0.05 → ~6 секунд).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import config
import content
import keyboards as kb
import scheduler
import utils
from handlers.common import make_router, safe_edit

router = make_router("sos")

# Один флаг остановки на пользователя: user_id → Event
_stop_flags: dict[int, asyncio.Event] = {}


@router.message(Command("sos"))
async def sos_entry(message: Message) -> None:
    await message.answer(content.SOS_INTRO, reply_markup=kb.sos_intro_kb())


@router.callback_query(kb.SosCB.filter(F.action == "stop"))
async def cb_stop(cb: CallbackQuery) -> None:
    flag = _stop_flags.get(cb.from_user.id)
    if flag:
        flag.set()
    await cb.answer("Останавливаю")


@router.callback_query(kb.SosCB.filter(F.action == "tips"))
async def cb_tips(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    await bot.send_message(
        cb.from_user.id,
        content.SOS_TIPS_HEADER + "\n\n" + utils.render_distractions(),
        reply_markup=kb.sos_after_kb(),
    )


@router.callback_query(kb.SosCB.filter(F.action == "breathe"))
async def cb_breathe(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    user_id = cb.from_user.id

    existing = _stop_flags.get(user_id)
    if existing is not None and not existing.is_set():
        await bot.send_message(user_id, content.SOS_ALREADY_RUNNING)
        return

    stop = asyncio.Event()
    _stop_flags[user_id] = stop
    cycles = max(1, int(content.SOS_CYCLES))

    try:
        message = await safe_edit(
            cb, bot, content.SOS_BREATH_INTRO.format(cycles=cycles), kb.sos_stop_kb()
        )
        await _pause(stop, 3)

        for cycle in range(1, cycles + 1):
            for phase_no, (phase, seconds, hint) in enumerate(content.SOS_PHASES, start=1):
                if stop.is_set():
                    break
                text = content.SOS_PHASE.format(
                    cycle=cycle,
                    cycles=cycles,
                    phase=phase,
                    seconds=seconds,
                    hint=hint,
                    dots="●" * phase_no + "○" * (len(content.SOS_PHASES) - phase_no),
                )
                message = await _edit_or_send(message, bot, user_id, text, kb.sos_stop_kb())
                if await _pause(stop, seconds):
                    break
            if stop.is_set():
                break

        final_header = content.SOS_STOPPED if stop.is_set() else content.SOS_DONE
        await _edit_or_send(
            message, bot, user_id,
            final_header + "\n\n" + utils.render_distractions(),
            kb.sos_after_kb(),
        )
    finally:
        _stop_flags.pop(user_id, None)


@router.callback_query(kb.SosCB.filter(F.action == "timer"))
async def cb_timer(cb: CallbackQuery, bot: Bot) -> None:
    """Правило «отложить на 10 минут» с реальным напоминанием."""
    await cb.answer()
    ok = scheduler.schedule_delayed_message(bot, cb.from_user.id, content.SOS_TIMER_DONE, delay_seconds=600)
    await bot.send_message(cb.from_user.id, content.SOS_TIMER_SET if ok else content.SOS_TIMER_UNAVAILABLE)


@router.callback_query(kb.SosCB.filter(F.action == "better"))
async def cb_better(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer("Красиво 👍")
    await bot.send_message(cb.from_user.id, content.SOS_BETTER)


@router.callback_query(kb.SosCB.filter(F.action == "still"))
async def cb_still(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    await bot.send_message(cb.from_user.id, content.SOS_STILL_HARD, reply_markup=kb.sos_intro_kb())


# ------------------------------------------------------------- внутренние ---

async def _pause(stop: asyncio.Event, seconds: float) -> bool:
    """Ждём seconds (с учётом масштаба демо). True — если нажали «Стоп»."""
    timeout = max(0.01, seconds * config.SOS_TIME_SCALE)
    try:
        await asyncio.wait_for(stop.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _edit_or_send(message: Message | None, bot: Bot, chat_id: int, text: str, markup) -> Message | None:
    """Редактируем сообщение практики; если Telegram отказал — шлём новое и продолжаем с него."""
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return message
        except TelegramBadRequest:
            pass
    try:
        return await bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramBadRequest:
        return message
