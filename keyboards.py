"""
keyboards.py — все кнопки и callback-схемы.

CallbackData — типизированная схема кнопки: вместо «магических строк» вроде "myth_1"
получаем объект MythCB(action="fact", index=1). Aiogram сам сериализует его в строку
и обратно, а фильтр MythCB.filter(F.action == "fact") ловит нужные нажатия.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

import content

# --- подписи нижнего меню (используются и в фильтрах обработчиков) -----------
BTN_SOS = "🆘 Накрыло"
BTN_PROGRESS = "📊 Мой прогресс"
BTN_MYTHS = "🧠 Мифы"
BTN_HELP = "📞 Помощь"
BTN_SETTINGS = "⚙️ Настройки"
BTN_CHECKIN = "✅ Сегодня без никотина"


# --- схемы callback ---------------------------------------------------------
class OnboardCB(CallbackData, prefix="ob"):
    step: str    # consent | mode | date | cost | notify
    value: str


class MythCB(CallbackData, prefix="myth"):
    action: str  # fact | go | sources
    index: int = 0


class SosCB(CallbackData, prefix="sos"):
    action: str  # breathe | stop | tips | timer | better | still


class ResetCB(CallbackData, prefix="reset"):
    action: str  # ask | confirm | cancel | trigger
    value: str = ""


class SettingsCB(CallbackData, prefix="set"):
    action: str  # cost | date | hour | toggle | mode | checkin | delete | confirm_delete | cancel_delete
    value: str = ""


class FeedbackCB(CallbackData, prefix="fb"):
    question: str  # q1 | q2 | q3
    value: str


# --- нижнее меню ------------------------------------------------------------
def main_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=BTN_PROGRESS), KeyboardButton(text=BTN_SOS))
    builder.row(KeyboardButton(text=BTN_MYTHS), KeyboardButton(text=BTN_HELP))
    builder.row(KeyboardButton(text=BTN_SETTINGS))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Команды: /sos /progress /myths")


# --- онбординг --------------------------------------------------------------
def consent_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Согласен, начать", callback_data=OnboardCB(step="consent", value="yes").pack()))
    builder.row(InlineKeyboardButton(text="❌ Не согласен", callback_data=OnboardCB(step="consent", value="no").pack()))
    builder.row(InlineKeyboardButton(text="🎓 Что за проект?", callback_data=OnboardCB(step="consent", value="about").pack()))
    return builder.as_markup()


def mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🚭 Я уже не употребляю", callback_data=OnboardCB(step="mode", value="quit").pack()))
    builder.row(InlineKeyboardButton(text="👀 Пока употребляю, хочу отслеживать", callback_data=OnboardCB(step="mode", value="watch").pack()))
    return builder.as_markup()


def date_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Сегодня", callback_data=OnboardCB(step="date", value="today").pack()),
        InlineKeyboardButton(text="Вчера", callback_data=OnboardCB(step="date", value="yesterday").pack()),
    )
    builder.row(InlineKeyboardButton(text="📅 Ввести дату", callback_data=OnboardCB(step="date", value="manual").pack()))
    return builder.as_markup()


def cost_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*[
        InlineKeyboardButton(text=f"{v} ₽", callback_data=OnboardCB(step="cost", value=str(v)).pack())
        for v in (150, 250, 400)
    ])
    builder.row(InlineKeyboardButton(text="✍️ Своя сумма", callback_data=OnboardCB(step="cost", value="manual").pack()))
    return builder.as_markup()


def notify_kb(default_hour: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"🔔 В {default_hour}:00", callback_data=OnboardCB(step="notify", value=str(default_hour)).pack()),
        InlineKeyboardButton(text="🕗 Другое время", callback_data=OnboardCB(step="notify", value="manual").pack()),
    )
    builder.row(InlineKeyboardButton(text="🔕 Без напоминаний", callback_data=OnboardCB(step="notify", value="off").pack()))
    return builder.as_markup()


# --- трекер -----------------------------------------------------------------
def progress_kb(mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if mode == "watch":
        builder.row(InlineKeyboardButton(text=BTN_CHECKIN, callback_data=SettingsCB(action="checkin").pack()))
        builder.row(InlineKeyboardButton(text="🚭 Перехожу в режим отказа", callback_data=SettingsCB(action="mode", value="quit").pack()))
    builder.row(
        InlineKeyboardButton(text="🆘 Накрыло", callback_data=SosCB(action="tips").pack()),
        InlineKeyboardButton(text="🔄 Сбросить счётчик", callback_data=ResetCB(action="ask").pack()),
    )
    builder.row(InlineKeyboardButton(text="📝 Анкета (3 вопроса)", callback_data=FeedbackCB(question="q1", value="start").pack()))
    return builder.as_markup()


def reset_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Да, начать заново", callback_data=ResetCB(action="confirm").pack()))
    builder.row(InlineKeyboardButton(text="Отмена", callback_data=ResetCB(action="cancel").pack()))
    return builder.as_markup()


def triggers_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        InlineKeyboardButton(text=label, callback_data=ResetCB(action="trigger", value=key).pack())
        for key, label in content.RELAPSE_TRIGGERS
    ]
    builder.add(*buttons)
    builder.adjust(2)
    return builder.as_markup()


# --- SOS --------------------------------------------------------------------
def sos_intro_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="▶️ Начать дыхание (2 мин)", callback_data=SosCB(action="breathe").pack()))
    builder.row(InlineKeyboardButton(text="🧊 Только приёмы", callback_data=SosCB(action="tips").pack()))
    return builder.as_markup()


def sos_stop_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏹ Стоп", callback_data=SosCB(action="stop").pack())
    ]])


def sos_after_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏳ Таймер на 10 минут", callback_data=SosCB(action="timer").pack()))
    builder.row(
        InlineKeyboardButton(text="👍 Отпустило", callback_data=SosCB(action="better").pack()),
        InlineKeyboardButton(text="😣 Всё ещё тянет", callback_data=SosCB(action="still").pack()),
    )
    return builder.as_markup()


# --- мифы -------------------------------------------------------------------
def myth_kb(index: int, total: int, show_fact: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if not show_fact:
        builder.row(InlineKeyboardButton(text="✅ Показать факт", callback_data=MythCB(action="fact", index=index).pack()))
    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=MythCB(action="go", index=index - 1).pack()))
    if index < total - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=MythCB(action="go", index=index + 1).pack()))
    if nav:
        builder.row(*nav)
    if show_fact and index == total - 1:
        builder.row(InlineKeyboardButton(text="📚 Источники", callback_data=MythCB(action="sources").pack()))
        builder.row(InlineKeyboardButton(text="🆘 Накрыло — помочь сейчас", callback_data=SosCB(action="tips").pack()))
    return builder.as_markup()


# --- настройки --------------------------------------------------------------
def settings_kb(notify_enabled: bool, mode: str = "quit") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💸 Изменить расход", callback_data=SettingsCB(action="cost").pack()))
    builder.row(InlineKeyboardButton(text="📅 Изменить дату отказа", callback_data=SettingsCB(action="date").pack()))
    builder.row(InlineKeyboardButton(text="🕗 Время утреннего сообщения", callback_data=SettingsCB(action="hour").pack()))
    builder.row(InlineKeyboardButton(
        text="🔕 Выключить утро" if notify_enabled else "🔔 Включить утро",
        callback_data=SettingsCB(action="toggle").pack(),
    ))
    if mode == "watch":
        builder.row(InlineKeyboardButton(text="🚭 Перейти в режим отказа", callback_data=SettingsCB(action="mode", value="quit").pack()))
    else:
        builder.row(InlineKeyboardButton(text="👀 Перейти в режим наблюдения", callback_data=SettingsCB(action="mode", value="watch").pack()))
    builder.row(InlineKeyboardButton(text="🗑 Удалить все мои данные", callback_data=SettingsCB(action="delete").pack()))
    return builder.as_markup()


def delete_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Да, удалить всё", callback_data=SettingsCB(action="confirm_delete").pack()))
    builder.row(InlineKeyboardButton(text="Отмена", callback_data=SettingsCB(action="cancel_delete").pack()))
    return builder.as_markup()


# --- анкета -----------------------------------------------------------------
def feedback_q1_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=str(i), callback_data=FeedbackCB(question="q1", value=str(i)).pack())
        for i in range(1, 6)
    ]])


def feedback_q2_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, label in content.FEEDBACK_Q2_OPTIONS:
        builder.row(InlineKeyboardButton(text=label, callback_data=FeedbackCB(question="q2", value=key).pack()))
    return builder.as_markup()


def feedback_q3_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=content.FEEDBACK_SKIP, callback_data=FeedbackCB(question="q3", value="skip").pack())
    ]])


def feedback_invite_kb() -> InlineKeyboardMarkup:
    """Кнопка под приглашением к анкете (приходит на 7-й и 30-й день)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📝 Ответить на 3 вопроса", callback_data=FeedbackCB(question="q1", value="start").pack())
    ]])
