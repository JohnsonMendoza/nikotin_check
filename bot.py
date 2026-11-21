"""
bot.py — точка входа. Собирает бота, подключает сценарии и запускает один из режимов:

  MODE=polling  python bot.py
      Бот сам опрашивает Telegram. Подходит для ноутбука и VPS. Ничего, кроме
      интернета, не нужно: ни домена, ни открытого порта.

  MODE=webhook  python bot.py
      Поднимается HTTP-сервер (aiohttp), Telegram сам присылает апдейты.
      Нужен для бесплатного хостинга (Render и т. п.), где есть только web-сервис.
      Дополнительно доступны адреса:
        GET /health          — проверка «жив ли сервис» (и keep-alive пинг)
        GET /cron/<CRON_KEY> — запуск утренней рассылки внешним планировщиком
                               (необязательный параметр ?hour=9)

Служебные команды:
  python bot.py --set-webhook     — зарегистрировать webhook в Telegram и выйти
  python bot.py --delete-webhook  — снять webhook (нужно перед возвратом к polling)
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

import config
import content
import db
import scheduler
from handlers import get_routers

# INFO — в логах нет текстов сообщений пользователей (это важно для анонимности).
# НЕ включай DEBUG на боевом запуске: тогда aiogram печатает содержимое апдейтов.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("bot")

# Список команд для кнопки «Меню» в Telegram.
BOT_COMMANDS = [
    BotCommand(command="start", description="Начать / вернуться"),
    BotCommand(command="sos", description="🆘 Накрыло — помощь за 2 минуты"),
    BotCommand(command="progress", description="📊 Дни без никотина и деньги"),
    BotCommand(command="myths", description="🧠 Мифы и факты"),
    BotCommand(command="reset", description="🔄 Срыв — начать заново"),
    BotCommand(command="feedback", description="📝 Анкета: 3 вопроса"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="help", description="📞 Живая помощь"),
    BotCommand(command="about", description="🎓 О проекте"),
    BotCommand(command="delete", description="🗑 Удалить мои данные"),
]


def create_bot() -> Bot:
    """Bot с HTML-разметкой по умолчанию (тексты в content.py используют <b> и <i>)."""
    session = AiohttpSession(proxy=config.PROXY_URL) if config.PROXY_URL else None
    return Bot(
        token=config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    """Dispatcher: маршрутизация апдейтов + машина состояний онбординга/анкеты."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(*get_routers())
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    dp.errors.register(on_error)
    return dp


async def on_startup(bot: Bot) -> None:
    await db.init_db()
    await bot.set_my_commands(BOT_COMMANDS)
    scheduler.start_scheduler(bot)
    me = await bot.get_me()
    log.info("Бот @%s запущен. Режим: %s. База: %s", me.username, config.MODE, config.DB_PATH)


async def on_shutdown(bot: Bot) -> None:
    scheduler.stop_scheduler()
    log.info("Остановлено корректно.")


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    """Любая необработанная ошибка не должна ронять бота и молчать в чате."""
    log.exception("Ошибка при обработке апдейта: %s", event.exception)
    update = event.update
    try:
        if update.message:
            await bot.send_message(update.message.chat.id, content.ERR_GENERIC)
        elif update.callback_query:
            await bot.send_message(update.callback_query.from_user.id, content.ERR_GENERIC)
    except Exception:  # noqa: BLE001 — сообщать об ошибке ошибки уже некому
        pass
    return True


# ---------------------------------------------------------------- polling ---

async def run_polling() -> None:
    bot = create_bot()
    dp = create_dispatcher()
    # Если раньше стоял webhook, polling не получит апдейты — снимаем его.
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


# ---------------------------------------------------------------- webhook ---

async def run_webhook() -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    bot = create_bot()
    dp = create_dispatcher()

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def cron(request: web.Request) -> web.Response:
        if not config.CRON_KEY or request.match_info.get("key") != config.CRON_KEY:
            return web.Response(status=403, text="forbidden")
        raw_hour = request.query.get("hour")
        hour = int(raw_hour) if raw_hour and raw_hour.isdigit() else None
        sent = await scheduler.send_daily_notifications(request.app["bot"], hour)
        return web.json_response({"sent": sent, "hour": hour})

    await bot.set_webhook(
        config.WEBHOOK_URL,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    log.info("Webhook зарегистрирован: %s", config.WEBHOOK_URL)

    app = web.Application()
    app["bot"] = bot
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=config.WEBHOOK_SECRET_PATH)
    setup_application(app, dp, bot=bot)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/cron/{key}", cron)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.PORT)
    await site.start()
    log.info("HTTP-сервер слушает 0.0.0.0:%s", config.PORT)
    try:
        await asyncio.Event().wait()  # работаем, пока процесс жив
    finally:
        await runner.cleanup()
        await bot.session.close()


# ------------------------------------------------------- служебные команды --

async def webhook_cli(delete: bool = False) -> None:
    bot = create_bot()
    try:
        if delete:
            await bot.delete_webhook(drop_pending_updates=True)
            print("Webhook снят. Теперь можно запускать MODE=polling.")
        else:
            config.validate()
            await bot.set_webhook(config.WEBHOOK_URL, drop_pending_updates=True)
            info = await bot.get_webhook_info()
            print("Webhook установлен:", info.url)
            print("Проверка:", info.url or "—", "| ошибок:", info.last_error_message or "нет")
    finally:
        await bot.session.close()


def main() -> None:
    if "--set-webhook" in sys.argv:
        asyncio.run(webhook_cli(delete=False))
        return
    if "--delete-webhook" in sys.argv:
        asyncio.run(webhook_cli(delete=True))
        return

    config.validate()
    try:
        asyncio.run(run_webhook() if config.MODE == "webhook" else run_polling())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено пользователем.")


if __name__ == "__main__":
    main()
