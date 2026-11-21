"""
handlers/navigation.py — кнопки нижнего меню. Работают всегда, даже если бот
ждёт ввод в другом сценарии: состояние сбрасывается и вызывается нужный экран.
Этот роутер подключается первым, поэтому меню никогда не «залипает».
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import content
import keyboards as kb
from handlers import core, myths, sos
from handlers.common import make_router

router = make_router("navigation")


@router.message(F.text == kb.BTN_PROGRESS)
async def btn_progress(message: Message, state: FSMContext) -> None:
    await state.clear()
    await core.show_progress(message)


@router.message(F.text == kb.BTN_SOS)
async def btn_sos(message: Message, state: FSMContext) -> None:
    await state.clear()
    await sos.sos_entry(message)


@router.message(F.text == kb.BTN_MYTHS)
async def btn_myths(message: Message, state: FSMContext) -> None:
    await state.clear()
    await myths.myths_entry(message)


@router.message(F.text == kb.BTN_HELP)
async def btn_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await core.show_help(message)


@router.message(F.text == kb.BTN_SETTINGS)
async def btn_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await core.show_settings(message, state)


@router.message(F.text == kb.BTN_CHECKIN)
async def btn_checkin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await core.do_checkin(message.from_user.id, message.bot, message.chat.id)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(content.MENU_HINT, reply_markup=kb.main_menu())
