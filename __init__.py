"""
handlers/__init__.py — порядок подключения роутеров.

Порядок важен: aiogram передаёт апдейт первому подошедшему обработчику.
  1. navigation — кнопки меню (работают из любого состояния)
  2. sos, myths, feedback — сценарии со своими командами и состояниями
  3. core — трекер, настройки, помощь, админ
  4. onboarding — /start и текстовые шаги настройки
  5. fallback — всё остальное
"""

from __future__ import annotations

from aiogram import Router

from handlers import core, fallback, feedback, myths, navigation, onboarding, sos


def get_routers() -> list[Router]:
    return [
        navigation.router,
        sos.router,
        myths.router,
        feedback.router,
        core.router,
        onboarding.router,
        fallback.router,
    ]
