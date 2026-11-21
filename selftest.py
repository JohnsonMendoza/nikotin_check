"""
selftest.py — офлайн-проверка бота БЕЗ токена и БЕЗ интернета.

Что делает:
  * подменяет транспорт Telegram «фальшивой сессией» (сообщения никуда не уходят);
  * прогоняет полный путь пользователя: /start → согласие → онбординг → /progress →
    /myths → /sos (дыхание) → /reset → анкета → /settings → /delete;
  * проверяет расчёты (дни, деньги, склонения), рассылку и защиту от повторов;
  * проверяет анонимность схемы БД и обезличенность выгрузки;
  * печатает стенограмму диалога и сохраняет её в demo_transcript.txt
    (удобно приложить к курсовому как демонстрацию работы продукта).

Запуск:  python selftest.py
Возвращает код 0, если все проверки прошли, и 1, если что-то сломано.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

# --- окружение подставляем ДО импорта модулей проекта ------------------------
_TMP = Path(tempfile.mkdtemp(prefix="nicotine_selftest_"))
os.environ["BOT_TOKEN"] = "123456789:AA-selftest-local-token-not-real"
os.environ["DB_PATH"] = str(_TMP / "selftest.sqlite3")
os.environ["ADMIN_ID"] = "1001"
os.environ["SOS_TIME_SCALE"] = "0.01"      # практика 4-7-8 прогоняется за ~1 секунду
os.environ["MODE"] = "polling"
os.environ["TIMEZONE"] = "Europe/Moscow"

from aiogram import Bot, Dispatcher                                    # noqa: E402
from aiogram.client.session.base import BaseSession                    # noqa: E402
from aiogram.filters.callback_data import CallbackData                 # noqa: E402
from aiogram.methods import TelegramMethod                             # noqa: E402
from aiogram.types import (                                          # noqa: E402
    Chat, CallbackQuery, InlineKeyboardMarkup, Message, Update, User,
)

import config                                                          # noqa: E402
import content                                                         # noqa: E402
import db                                                              # noqa: E402
import keyboards as kb                                                 # noqa: E402
import scheduler                                                       # noqa: E402
import utils                                                           # noqa: E402
from bot import create_dispatcher                                      # noqa: E402

BOT_ID = 123456789
USER_ID = 1001

RESULTS: list[tuple[bool, str]] = []
TRANSCRIPT: list[str] = []


def check(condition: bool, label: str) -> None:
    RESULTS.append((bool(condition), label))
    mark = "✅" if condition else "❌"
    print(f"{mark} {label}")


# ---------------------------------------------------------------------------
# Фальшивый транспорт: вместо HTTPS-запросов к api.telegram.org складываем
# исходящие сообщения в список и возвращаем правдоподобные объекты Telegram.
# ---------------------------------------------------------------------------
class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[Message] = []
        self.counter = 5000

    async def close(self) -> None:  # noqa: D102
        return None

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout=None):
        name = type(method).__name__
        self.counter += 1

        if name == "GetMe":
            return User(id=BOT_ID, is_bot=True, first_name="Дыши свободно", username="selftest_bot")

        if name in ("SendMessage", "SendDocument", "EditMessageText"):
            chat_id = int(getattr(method, "chat_id", USER_ID))
            text = getattr(method, "text", None) or "[документ]"
            markup = getattr(method, "reply_markup", None)
            # В объекте Message Telegram хранит только inline-клавиатуру;
            # нижнее меню (ReplyKeyboardMarkup) в сообщение не подставляется.
            if not isinstance(markup, InlineKeyboardMarkup):
                markup = None
            message = Message(
                message_id=self.counter,
                date=dt.datetime.now(dt.timezone.utc),
                chat=Chat(id=chat_id, type="private"),
                from_user=User(id=BOT_ID, is_bot=True, first_name="Дыши свободно"),
                text=text,
                reply_markup=markup,
            )
            # Как и настоящий транспорт, привязываем объект к боту (иначе message.edit_text не работает)
            message = message.as_(bot)
            self.messages.append(message)
            prefix = "БОТ (правка)" if name == "EditMessageText" else "БОТ"
            TRANSCRIPT.append(f"{prefix}: {text}")
            return message

        # AnswerCallbackQuery, SetMyCommands, DeleteWebhook, SetWebhook, SendChatAction...
        return True


class Simulator:
    """Мини-«Telegram»: отправляет апдейты в диспетчер и нажимает реальные кнопки."""

    def __init__(self, bot: Bot, dp: Dispatcher, session: FakeSession, user_id: int = USER_ID) -> None:
        self.bot, self.dp, self.session, self.user_id = bot, dp, session, user_id
        self.update_id = 0
        self.message_id = 0

    async def send(self, text: str) -> None:
        self.update_id += 1
        self.message_id += 1
        TRANSCRIPT.append(f"\nПОЛЬЗОВАТЕЛЬ: {text}")
        update = Update(
            update_id=self.update_id,
            message=Message(
                message_id=self.message_id,
                date=dt.datetime.now(dt.timezone.utc),
                chat=Chat(id=self.user_id, type="private"),
                from_user=User(id=self.user_id, is_bot=False, first_name="Студент"),
                text=text,
            ),
        )
        await self.dp.feed_update(self.bot, update)

    async def click(self, data: str | CallbackData, contains: str | None = None) -> None:
        packed = data.pack() if isinstance(data, CallbackData) else data
        target = self._find_message(packed, contains)
        if target is None:
            raise AssertionError(f"Кнопка не найдена: {packed}")
        self.update_id += 1
        label = self._button_label(target, packed)
        TRANSCRIPT.append(f"\nПОЛЬЗОВАТЕЛЬ: [нажата кнопка] {label}")
        update = Update(
            update_id=self.update_id,
            callback_query=CallbackQuery(
                id=str(self.update_id),
                from_user=User(id=self.user_id, is_bot=False, first_name="Студент"),
                chat_instance="selftest",
                message=target,
                data=packed,
            ),
        )
        await self.dp.feed_update(self.bot, update)

    def _find_message(self, packed: str, contains: str | None) -> Message | None:
        for message in reversed(self.session.messages):
            markup = message.reply_markup
            if not markup or not getattr(markup, "inline_keyboard", None):
                continue
            if contains and contains not in (message.text or ""):
                continue
            for row in markup.inline_keyboard:
                for button in row:
                    if button.callback_data == packed:
                        return message
        return None

    @staticmethod
    def _button_label(message: Message, packed: str) -> str:
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data == packed:
                    return button.text
        return packed

    def last_texts(self, n: int = 1) -> str:
        return "\n".join(m.text or "" for m in self.session.messages[-n:])


async def scenario() -> Dispatcher:
    session = FakeSession()
    bot = Bot(token=config.BOT_TOKEN, session=session)
    dp = create_dispatcher()
    await db.init_db()
    scheduler.init_scheduler().start(paused=False)
    sim = Simulator(bot, dp, session)

    print("\n=== 1. Онбординг и согласие ===")
    await sim.send("/start")
    check("анонимность" in sim.last_texts(2).lower(), "/start объясняет анонимность")
    check(await db.get_user(USER_ID) is None, "до согласия в базе нет ни одной записи")

    await sim.click(kb.OnboardCB(step="consent", value="about"))
    check("Обучение служением" in sim.last_texts(), "кнопка «Что за проект?» рассказывает о проекте")

    await sim.click(kb.OnboardCB(step="consent", value="yes"))
    user = await db.get_user(USER_ID)
    check(user is not None and user["consent"] == 1, "согласие зафиксировано")

    await sim.click(kb.OnboardCB(step="mode", value="quit"))
    await sim.click(kb.OnboardCB(step="date", value="yesterday"))
    await sim.click(kb.OnboardCB(step="cost", value="250"))
    await sim.click(kb.OnboardCB(step="notify", value="9"))
    user = await db.get_user(USER_ID)
    yesterday = (utils.today_local() - dt.timedelta(days=1)).isoformat()
    check(user["quit_date"] == yesterday, "дата отказа = вчера")
    check(user["daily_cost"] == 250 and user["notify_hour"] == 9, "расход и час уведомления сохранены")
    check("Готово" in sim.last_texts(), "онбординг завершён приветственным экраном")

    print("\n=== 2. Трекер и расчёты ===")
    await sim.send("/progress")
    text = sim.last_texts()
    check("Без никотина: <b>1 день</b>" in text, "счётчик показывает 1 полный день")
    check("250 ₽" in text, "сэкономлено 250 ₽ за один день")
    check("7 500 ₽" in text and "91 250 ₽" in text, "проекция на месяц и год посчитана")
    check(utils.plural(2, ("день", "дня", "дней")) == "дня" and utils.plural(5, ("день", "дня", "дней")) == "дней", "склонение дней работает")
    check(utils.parse_user_date("31.02.2026") is None, "некорректная дата отклоняется")
    future = (utils.today_local() + dt.timedelta(days=3)).strftime("%d.%m.%Y")
    check(utils.parse_user_date(future) is None, "дата из будущего отклоняется")

    print("\n=== 3. Мифы ===")
    await sim.send("/myths")
    check("Миф 1 из 5" in sim.last_texts(), "первая карточка из пяти")
    for index in range(len(content.MYTHS)):
        await sim.click(kb.MythCB(action="fact", index=index))
        fact_text = sim.last_texts()
        check("Факт" in fact_text and "Источник" in fact_text, f"карточка {index + 1}: факт и источник на месте")
        if index < len(content.MYTHS) - 1:
            await sim.click(kb.MythCB(action="go", index=index + 1))
    await sim.click(kb.MythCB(action="sources"))
    check("BMJ" in sim.last_texts(), "список источников доступен")

    print("\n=== 4. SOS: дыхание 4-7-8 и приёмы ===")
    await sim.send("/sos")
    check("волна" in sim.last_texts().lower(), "/sos объясняет природу тяги")
    await sim.click(kb.SosCB(action="breathe"))
    final = sim.last_texts()
    check("Две минуты прошли" in final, "практика доведена до конца")
    check("Правило 10 минут" in final and "Стакан воды" in final and "прогулка" in final.lower(), "все три приёма из брифа показаны")
    phase_texts = [m.text for m in session.messages if m.text and "Цикл" in m.text]
    check(len(phase_texts) >= 12, f"фаз дыхания показано: {len(phase_texts)} (4 цикла × 3 фазы)")
    await sim.click(kb.SosCB(action="timer"))
    check("Таймер поставлен" in sim.last_texts(), "таймер «отложить на 10 минут» ставится")
    await sim.click(kb.SosCB(action="better"))
    check("справился" in sim.last_texts(), "поддержка после успешного переживания тяги")

    print("\n=== 5. Срыв: сброс без осуждения ===")
    await sim.send("/reset")
    await sim.click(kb.ResetCB(action="confirm"))
    reset_text = sim.last_texts()
    check("не провал" in sim.last_texts(2) and "слаб" in sim.last_texts(2), "сброс сформулирован без осуждения")
    await sim.click(kb.ResetCB(action="trigger", value="stress"))
    user = await db.get_user(USER_ID)
    check(user["quit_date"] == utils.today_local().isoformat(), "счётчик начат заново с сегодня")
    check(user["relapse_count"] == 1, "срыв посчитан как попытка")
    stats = await db.all_stats()
    check(("stress", 1) in stats["triggers"], "триггер срыва сохранён обезличенно")

    await sim.send("/reset")
    await sim.click(kb.ResetCB(action="confirm"))
    check("и так начинается с сегодняшнего дня" in sim.last_texts(), "повторный сброс в тот же день обработан корректно")
    await sim.click(kb.ResetCB(action="trigger", value="habit"))
    check((await db.get_user(USER_ID))["relapse_count"] == 2, "повторный срыв тоже учтён как попытка")

    print("\n=== 6. Режим наблюдения и отметки дней ===")
    await sim.send("/settings")
    await sim.click(kb.SettingsCB(action="mode", value="watch"))
    await sim.send("✅ Сегодня без никотина")
    check("день без никотина" in sim.last_texts(), "отметка дня работает")
    await sim.click(kb.SettingsCB(action="checkin"))
    check("уже отмечен" in sim.last_texts(), "повторная отметка того же дня не удваивает счёт")
    check(await db.count_clean_days(USER_ID) == 1, "в базе ровно один отмеченный день")
    await sim.send("/progress")
    check("Режим наблюдения" in sim.last_texts(), "/progress учитывает режим наблюдения")
    await sim.click(kb.SettingsCB(action="mode", value="quit"))
    check("режим отказа" in sim.last_texts(2).lower(), "возврат в режим отказа работает")
    await sim.send("✅ Сегодня без никотина")
    check("дни считаются сами" in sim.last_texts(), "в режиме отказа отметки дней не нужны")

    print("\n=== 7. Анкета из 3 вопросов ===")
    await sim.send("/feedback")
    await sim.click(kb.FeedbackCB(question="q1", value="5"))
    await sim.click(kb.FeedbackCB(question="q2", value="sos"))
    await sim.send("Хочется больше практик и меньше текста")
    check("Спасибо" in sim.last_texts(), "анкета принята")
    stats = await db.all_stats()
    check(stats["feedback_n"] == 1 and stats["q1_avg"] == 5.0, "ответы попали в обезличенную статистику")

    await sim.send("/feedback")
    await sim.click(kb.FeedbackCB(question="q1", value="4"))
    await sim.click(kb.FeedbackCB(question="q2", value="tracker"))
    await sim.send("позвони мне 89112345678")
    check("анонимной" in sim.last_texts(), "свободный текст с телефоном отклоняется")
    await sim.click(kb.FeedbackCB(question="q3", value="skip"))
    check("Спасибо" in sim.last_texts(), "анкету можно завершить пропуском текста")

    print("\n=== 8. Помощь, настройки, админ ===")
    await sim.send("/help")
    check("8-800-200-0-200" in sim.last_texts(), "федеральная линия помощи на месте")
    check("494-92-03" in sim.last_texts(), "телефон института на месте")
    await sim.send("/settings")
    await sim.click(kb.SettingsCB(action="cost"))
    await sim.send("триста")
    check("Нужно одно число" in sim.last_texts(), "нечисловой расход отклоняется")
    await sim.send("300")
    user = await db.get_user(USER_ID)
    check(user["daily_cost"] == 300, "расход изменён через настройки")
    await sim.click(kb.SettingsCB(action="toggle"))
    check((await db.get_user(USER_ID))["notify_enabled"] == 0, "утренние уведомления выключаются")
    await sim.click(kb.SettingsCB(action="toggle"))
    check((await db.get_user(USER_ID))["notify_enabled"] == 1, "и включаются обратно")

    await sim.send("/stats")
    check("Обезличенная сводка" in sim.last_texts(), "админ видит сводку")
    check("1001" not in sim.last_texts(), "в сводке нет user_id")

    print("\n=== 9. Утренняя рассылка ===")
    await db.update_user(USER_ID, notify_hour=9, notify_enabled=1, last_notify_day=None)
    sent = await scheduler.send_daily_notifications(bot, hour=9)
    check(sent == 1, "утреннее сообщение отправлено")
    check("без никотина" in sim.last_texts(), "в сообщении есть текущий результат")
    sent_again = await scheduler.send_daily_notifications(bot, hour=9)
    check(sent_again == 0, "повторно за тот же день не отправляется")
    sent_other = await scheduler.send_daily_notifications(bot, hour=3)
    check(sent_other == 0, "в чужой час не отправляется")

    print("\n=== 10. Анонимность и право на удаление ===")
    schema = db.SCHEMA.lower()
    check(not any(word in schema for word in ("username", "first_name", "last_name", "phone", "email")),
          "в схеме БД нет полей для имени, username, телефона, почты")
    csv_path = _TMP / "feedback.csv"
    await db.export_feedback_csv(csv_path)
    csv_head = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    check("user_id" not in csv_head, "в выгрузке анкеты нет user_id")

    await sim.send("/delete")
    await sim.click(kb.SettingsCB(action="confirm_delete"))
    check(await db.get_user(USER_ID) is None, "/delete удаляет профиль")
    check(await db.count_clean_days(USER_ID) == 0, "/delete удаляет отметки дней")
    stats = await db.all_stats()
    check(stats["feedback_n"] == 0 and stats["relapses"] == 0, "/delete удаляет анкету и историю срывов")

    print("\n=== 11. Устойчивость к ошибке ===")
    original_get_user = db.get_user

    async def broken_get_user(*_args, **_kwargs):
        raise RuntimeError("искусственная поломка для проверки обработчика ошибок")

    db.get_user = broken_get_user
    await sim.send("/progress")
    db.get_user = original_get_user
    check("Что-то пошло не так" in sim.last_texts(), "при ошибке бот отвечает пользователю и не падает")

    print("\n=== 12. Целостность контента ===")
    check(len(content.MYTHS) == 5, "мифов ровно 5, как в брифе")
    check(all(card.get("fact") and card.get("source") for card in content.MYTHS), "у каждого мифа есть факт и источник")
    check(len(content.DISTRACTIONS) == 3, "приёмов отвлечения ровно 3")
    check(sum(seconds for _, seconds, _ in content.SOS_PHASES) * content.SOS_CYCLES >= 60,
          "дыхательная практика длится не меньше минуты")
    check(len(content.DAILY_MESSAGES) >= 7, "утренних сообщений минимум на неделю")
    check(config.BOT_TOKEN not in Path("content.py").read_text(encoding="utf-8"), "в текстах нет токена")

    scheduler.stop_scheduler()
    await bot.session.close()
    return dp


async def webhook_scenario(dp: Dispatcher) -> None:
    """Проверка режима для бесплатного хостинга: HTTP-сервер, webhook, /health, /cron."""
    import socket

    import aiohttp

    import bot as bot_module

    print("\n=== 13. Режим webhook (хостинг) ===")
    session = FakeSession()
    original_create_bot = bot_module.create_bot
    original_create_dp = bot_module.create_dispatcher
    bot_module.create_bot = lambda: Bot(token=config.BOT_TOKEN, session=session)
    # Роутеры aiogram — одиночные объекты: переиспользуем уже собранный диспетчер.
    bot_module.create_dispatcher = lambda: dp

    config.MODE = "webhook"
    config.WEBHOOK_BASE_URL = "https://example.onrender.com"
    config.WEBHOOK_URL = config.WEBHOOK_BASE_URL + config.WEBHOOK_SECRET_PATH
    config.CRON_KEY = "selftest-cron-key"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        config.PORT = sock.getsockname()[1]

    task = asyncio.create_task(bot_module.run_webhook())
    base = f"http://127.0.0.1:{config.PORT}"
    try:
        async with aiohttp.ClientSession() as http:
            alive = False
            for _ in range(60):
                try:
                    async with http.get(base + "/health") as response:
                        if response.status == 200:
                            alive = True
                            break
                except Exception:  # noqa: BLE001 — сервер ещё поднимается
                    pass
                await asyncio.sleep(0.1)
            check(alive, "webhook: HTTP-сервер поднялся, /health отвечает 200")

            payload = {
                "update_id": 90001,
                "message": {
                    "message_id": 1,
                    "date": int(dt.datetime.now(dt.timezone.utc).timestamp()),
                    "chat": {"id": 2002, "type": "private"},
                    "from": {"id": 2002, "is_bot": False, "first_name": "Студент"},
                    "text": "/start",
                },
            }
            async with http.post(base + config.WEBHOOK_SECRET_PATH, json=payload) as response:
                check(response.status == 200, "webhook: апдейт принят (200 OK)")
            await asyncio.sleep(0.4)
            check(any("Привет" in (m.text or "") for m in session.messages),
                  "webhook: /start реально обработан и ответ ушёл пользователю")

            async with http.get(base + "/cron/wrong-key") as response:
                check(response.status == 403, "webhook: /cron закрыт ключом")
            async with http.get(base + f"/cron/{config.CRON_KEY}?hour=9") as response:
                data = await response.json()
                check(response.status == 200 and "sent" in data, "webhook: /cron запускает рассылку")
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        bot_module.create_bot = original_create_bot
        bot_module.create_dispatcher = original_create_dp
        scheduler.stop_scheduler()


def main() -> int:
    print("Офлайн-проверка бота «Дыши свободно» (без токена и без интернета)")
    print(f"Временная база: {config.DB_PATH}")
    try:
        async def run_all() -> None:
            dp = await scenario()
            await webhook_scenario(dp)

        asyncio.run(run_all())
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        RESULTS.append((False, f"сценарий упал: {type(exc).__name__}: {exc}"))

    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\nИТОГ: {passed}/{total} проверок пройдено")

    transcript_path = Path(__file__).resolve().parent / "demo_transcript.txt"
    transcript_path.write_text(
        "Стенограмма работы бота (офлайн-прогон selftest.py)\n"
        f"Дата: {dt.datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        + "=" * 70 + "\n" + "\n".join(TRANSCRIPT),
        encoding="utf-8",
    )
    print(f"Стенограмма диалога: {transcript_path.name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
