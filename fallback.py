"""
handlers/fallback.py — ответ на всё непонятное. Подключается ПОСЛЕДНИМ,
чтобы не перехватывать сообщения, адресованные другим сценариям.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

import content
import keyboards as kb
from handlers.common import make_router

router = make_router("fallback")


@router.message()
async def unknown(message: Message) -> None:
    await message.answer(content.FALLBACK, reply_markup=kb.main_menu())
