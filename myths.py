"""
handlers/myths.py — интерактивные карточки «Миф → Факт».

Количество карточек берётся из content.MYTHS: добавишь шестой миф в content.py —
кнопки навигации и счётчик «N из M» пересчитаются сами, код править не нужно.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import content
import keyboards as kb
import utils
from handlers.common import make_router, safe_edit

router = make_router("myths")


@router.message(Command("myths"))
async def myths_entry(message: Message) -> None:
    total = len(content.MYTHS)
    if not total:
        return
    await message.answer(content.MYTHS_INTRO)
    await message.answer(utils.render_myth(0, show_fact=False), reply_markup=kb.myth_kb(0, total, False))


@router.callback_query(kb.MythCB.filter(F.action == "fact"))
async def cb_show_fact(cb: CallbackQuery, callback_data: kb.MythCB, bot: Bot) -> None:
    await cb.answer()
    index = callback_data.index
    await safe_edit(cb, bot, utils.render_myth(index, show_fact=True), kb.myth_kb(index, len(content.MYTHS), True))


@router.callback_query(kb.MythCB.filter(F.action == "go"))
async def cb_go(cb: CallbackQuery, callback_data: kb.MythCB, bot: Bot) -> None:
    await cb.answer()
    index = max(0, min(callback_data.index, len(content.MYTHS) - 1))
    await safe_edit(cb, bot, utils.render_myth(index, show_fact=False), kb.myth_kb(index, len(content.MYTHS), False))


@router.callback_query(kb.MythCB.filter(F.action == "sources"))
async def cb_sources(cb: CallbackQuery, bot: Bot) -> None:
    await cb.answer()
    await bot.send_message(cb.from_user.id, content.SOURCES_TEXT)
