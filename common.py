"""
handlers/common.py — маленькие помощники, общие для всех сценариев.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

import content
import db


def make_router(name: str) -> Router:
    """Роутер, который работает только в личных чатах (приватность по умолчанию)."""
    router = Router(name=name)
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    return router


async def require_profile(user_id: int, bot: Bot, chat_id: int) -> dict | None:
    """Возвращает профиль или сам объясняет, что нужен онбординг."""
    user = await db.get_user(user_id)
    if not user or not user["consent"] or not user["quit_date"]:
        await bot.send_message(chat_id, content.NEED_ONBOARDING)
        return None
    return user


async def safe_edit(cb: CallbackQuery, bot: Bot, text: str, reply_markup=None) -> Message | None:
    """Редактирует сообщение, а если Telegram не даёт (старое/удалённое) — присылает новое."""
    message = cb.message
    if isinstance(message, Message):
        try:
            result = await message.edit_text(text, reply_markup=reply_markup)
            return result if isinstance(result, Message) else message
        except TelegramBadRequest:
            pass
    return await bot.send_message(cb.from_user.id, text, reply_markup=reply_markup)
