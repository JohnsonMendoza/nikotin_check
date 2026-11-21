"""
utils.py — чистая логика: даты, деньги, склонения, сборка текстов из шаблонов content.py.

Здесь нет обращений к Telegram, поэтому всё это легко проверяется тестом selftest.py.
Тексты живут в content.py, расчёты — здесь. Это и есть разделение «контент / логика».
"""

from __future__ import annotations

import datetime as dt
import random
import re
from zoneinfo import ZoneInfo

import config
import content

TZ = ZoneInfo(config.TIMEZONE)


# ------------------------------------------------------------- время --------

def now_local() -> dt.datetime:
    return dt.datetime.now(TZ)


def today_local() -> dt.date:
    return now_local().date()


def parse_user_date(text: str) -> dt.date | None:
    """Понимает ДД.ММ.ГГГГ, ДД.ММ, ДД/ММ/ГГГГ. Будущее и мусор → None."""
    raw = text.strip().replace("/", ".").replace("-", ".")
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", raw)
    if not match:
        return None
    day, month, year = match.groups()
    today = today_local()
    year_i = int(year) if year else today.year
    try:
        result = dt.date(year_i, int(month), int(day))
    except ValueError:
        return None
    # «14.12» в январе — это, скорее всего, прошлый декабрь
    if not year and result > today:
        try:
            result = dt.date(year_i - 1, int(month), int(day))
        except ValueError:
            return None
    if result > today or (today - result).days > 365 * 20:
        return None
    return result


def days_since(iso_date: str | None) -> int:
    """Полных суток без никотина. Дата отказа = первый день без никотина (день 0)."""
    if not iso_date:
        return 0
    try:
        start = dt.date.fromisoformat(iso_date)
    except ValueError:
        return 0
    return max(0, (today_local() - start).days)


def fmt_date(iso_date: str | None) -> str:
    if not iso_date:
        return "—"
    try:
        return dt.date.fromisoformat(iso_date).strftime("%d.%m.%Y")
    except ValueError:
        return "—"


# ------------------------------------------------------------- деньги -------

def fmt_money(value: float) -> str:
    """1250.0 → '1 250 ₽'. Копейки не показываем: это оценка, а не бухгалтерия."""
    return f"{int(round(value)):,}".replace(",", " ") + " ₽"


def money_saved(days: int, daily_cost: float) -> float:
    return max(0.0, days * float(daily_cost or 0))


# --------------------------------------------------------- язык и прогресс --

def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(5, ('день','дня','дней')) → 'дней'."""
    n = abs(int(n)) % 100
    last = n % 10
    if 10 < n < 20:
        return forms[2]
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def days_word(n: int) -> str:
    return plural(n, ("день", "дня", "дней"))


def next_milestone(days: int) -> tuple[int, str]:
    for value, note in content.MILESTONES:
        if days < value:
            return value, note
    # дальше года — шагом по 90 дней
    value = ((days // 90) + 1) * 90
    return value, "очередные три месяца — уже про образ жизни"


def progress_bar(days: int, width: int = 10) -> str:
    """Текстовый прогресс-бар до следующей вехи: ▰▰▰▱▱▱▱▱▱▱ 3/7."""
    target, _ = next_milestone(days)
    prev = max([v for v, _ in content.MILESTONES if v <= days], default=0)
    span = max(1, target - prev)
    filled = min(width, max(0, round((days - prev) / span * width)))
    return "▰" * filled + "▱" * (width - filled) + f"  {days}/{target}"


def health_fact(days: int) -> str:
    fact = content.HEALTH_TIMELINE[0][1]
    for threshold, text in content.HEALTH_TIMELINE:
        if days >= threshold:
            fact = text
    return fact


# ------------------------------------------------------- сборка сообщений ---

def short_summary(user: dict, clean_days: int = 0) -> str:
    days = days_since(user.get("quit_date"))
    if user.get("mode") == "watch":
        return (
            f"👀 Режим наблюдения · ✅ {clean_days} дней без никотина · "
            f"💰 {fmt_money(money_saved(clean_days, user.get('daily_cost', 0)))}"
        )
    return (
        f"🗓 {days} {days_word(days)} без никотина · "
        f"💰 {fmt_money(money_saved(days, user.get('daily_cost', 0)))}"
    )


def render_progress(user: dict, clean_days: int = 0) -> str:
    """Полный отчёт /progress. Шаблоны берутся из content.py."""
    days = days_since(user.get("quit_date"))
    cost = float(user.get("daily_cost") or 0)

    if user.get("mode") == "watch":
        return content.PROGRESS_WATCH.format(
            days=days,
            days_word=days_word(days),
            clean=clean_days,
            money=fmt_money(money_saved(clean_days, cost)),
            bar=progress_bar(clean_days),
        )

    target, note = next_milestone(days)
    health = content.PROGRESS_HEALTH_NOTE.format(health_fact=health_fact(days))
    return content.PROGRESS_QUIT.format(
        days=days,
        days_word=days_word(days),
        current_day=days + 1,
        money=fmt_money(money_saved(days, cost)),
        money_month=fmt_money(money_saved(30, cost)),
        money_year=fmt_money(money_saved(365, cost)),
        bar=progress_bar(days),
        next_days=target,
        next_days_word=days_word(target),
        next_note=note,
        health=health,
        relapses=int(user.get("relapse_count") or 0),
    )


def render_daily(user: dict, clean_days: int = 0) -> str:
    """Утреннее сообщение: поддерживающая фраза (ротация) + текущие цифры."""
    days = days_since(user.get("quit_date"))
    cost = float(user.get("daily_cost") or 0)
    message = content.DAILY_MESSAGES[days % len(content.DAILY_MESSAGES)]
    if user.get("mode") == "watch":
        return content.DAILY_TEMPLATE_WATCH.format(
            message=message,
            clean=clean_days,
            days=days,
            money=fmt_money(money_saved(clean_days, cost)),
        )
    return content.DAILY_TEMPLATE.format(
        message=message,
        days=days,
        days_word=days_word(days),
        money=fmt_money(money_saved(days, cost)),
    )


def render_myth(index: int, show_fact: bool) -> str:
    """Карточка мифа. index — 0-based, наружу показывается index+1."""
    total = len(content.MYTHS)
    index = max(0, min(index, total - 1))
    card = content.MYTHS[index]
    if not show_fact:
        return content.MYTH_CARD.format(index=index + 1, total=total, myth=card["myth"])
    action = card.get("action") or ""
    action_block = content.MYTH_ACTION_BLOCK.format(action=action) if action else ""
    return content.MYTH_FACT_CARD.format(
        index=index + 1,
        total=total,
        myth=card["myth"],
        fact=card["fact"],
        action_block=action_block,
        source=card.get("source", "—"),
    )


def render_distractions() -> str:
    """Три основных приёма из брифа + один случайный дополнительный."""
    items = list(content.DISTRACTIONS)
    if content.SOS_EXTRA_TIPS:
        items.append(random.choice(content.SOS_EXTRA_TIPS))
    return "\n\n".join(items)


def render_help() -> str:
    psychologist = content.PSYCHOLOGIST_BLOCK.format(
        psy_name=content.PSY_NAME or "<i>Контакт психолога уточняется у лаборатории — пока звони по телефону института ниже.</i>",
        psy_contact=content.PSY_CONTACT,
        psy_hours=content.PSY_HOURS,
    )
    # убираем пустые строки, если поля контактов не заполнены
    psychologist = "\n".join(line for line in psychologist.splitlines() if line.strip())
    return content.HELP_TEXT.format(psychologist=psychologist)


def render_settings(user: dict) -> str:
    notify = (
        content.SETTINGS_NOTIFY_ON.format(hour=int(user.get("notify_hour") or 0))
        if user.get("notify_enabled")
        else content.SETTINGS_NOTIFY_OFF
    )
    return content.SETTINGS_TEXT.format(
        mode=content.SETTINGS_MODE_WATCH if user.get("mode") == "watch" else content.SETTINGS_MODE_QUIT,
        date=fmt_date(user.get("quit_date")),
        cost=fmt_money(float(user.get("daily_cost") or 0)),
        notify=notify,
    )


def render_admin_stats(stats: dict) -> str:
    q2_labels = dict(content.FEEDBACK_Q2_OPTIONS)
    trigger_labels = dict(content.RELAPSE_TRIGGERS)
    q2_top = ", ".join(f"{q2_labels.get(k, k)} — {v}" for k, v in stats["q2_counts"][:3]) or "пока нет ответов"
    triggers_top = ", ".join(f"{trigger_labels.get(k, k)} — {v}" for k, v in stats["triggers"][:3]) or "пока нет данных"
    return content.ADMIN_STATS.format(**stats, q2_top=q2_top, triggers_top=triggers_top)
