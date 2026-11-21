"""
handlers/feedback.py — мини-анкета из 3 вопросов (данные для отчёта по курсовому).

Вопросы и варианты ответов лежат в content.py. Ответы хранятся обезличенно:
в таблице feedback есть user_id (чтобы не спрашивать дважды), но в выгрузку CSV
и в админ-сводку он не попадает — только агрегаты и сами ответы.
"""

from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import content
import db
import keyboards as kb
from handlers.common import make_router, require_profile, safe_edit

router = make_router("feedback")

MAX_FREE_TEXT = 500  # символов: коротко и без риска получить «сочинение с ФИО»


class Feedback(StatesGroup):
    q3 = State()


@router.message(Command("feedback"))
async def feedback_entry(message: Message, state: FSMContext) -> None:
    await _start(message.from_user.id, message.bot, state)


async def _start(user_id: int, bot: Bot, state: FSMContext) -> None:
    user = await require_profile(user_id, bot, user_id)
    if not user:
        return
    await state.clear()
    if user["feedback_done"]:
        await bot.send_message(user_id, content.FEEDBACK_ALREADY)
    await bot.send_message(user_id, content.FEEDBACK_INTRO)
    await bot.send_message(user_id, content.FEEDBACK_Q1, reply_markup=kb.feedback_q1_kb())


@router.callback_query(kb.FeedbackCB.filter(F.question == "q1"))
async def cb_q1(cb: CallbackQuery, callback_data: kb.FeedbackCB, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    if callback_data.value == "start":       # кнопка «Анкета» из /progress
        await _start(cb.from_user.id, bot, state)
        return
    try:
        score = int(callback_data.value)
    except ValueError:
        return
    if not 1 <= score <= 5:
        return
    await state.update_data(q1=score)
    await safe_edit(cb, bot, content.FEEDBACK_Q2, kb.feedback_q2_kb())


@router.callback_query(kb.FeedbackCB.filter(F.question == "q2"))
async def cb_q2(cb: CallbackQuery, callback_data: kb.FeedbackCB, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    allowed = {key for key, _ in content.FEEDBACK_Q2_OPTIONS}
    if callback_data.value not in allowed:
        return
    await state.update_data(q2=callback_data.value)
    await state.set_state(Feedback.q3)
    await safe_edit(cb, bot, content.FEEDBACK_Q3, kb.feedback_q3_kb())


@router.callback_query(kb.FeedbackCB.filter(F.question == "q3"))
async def cb_q3_skip(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await cb.answer()
    await _save(cb.from_user.id, bot, state, free_text="")


@router.message(StateFilter(Feedback.q3), F.text)
async def on_q3_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    # Мягкая защита анонимности: не даём случайно сдать телефон/номер карты.
    if re.search(r"\d[\d\s\-()]{6,}\d", text):
        await message.answer(content.ERR_FEEDBACK_PERSONAL, reply_markup=kb.feedback_q3_kb())
        return
    await _save(message.from_user.id, message.bot, state, free_text=text[:MAX_FREE_TEXT])


async def _save(user_id: int, bot: Bot, state: FSMContext, free_text: str) -> None:
    data = await state.get_data()
    await state.clear()
    q1 = int(data.get("q1") or 0) or None
    q2 = str(data.get("q2") or "") or None
    if q1 is None and q2 is None:
        # анкета не начиналась — просто вежливо приглашаем
        await bot.send_message(user_id, content.FEEDBACK_INTRO)
        await bot.send_message(user_id, content.FEEDBACK_Q1, reply_markup=kb.feedback_q1_kb())
        return
    await db.save_feedback(user_id, q1, q2, free_text)
    await bot.send_message(user_id, content.FEEDBACK_THANKS, reply_markup=kb.main_menu())
