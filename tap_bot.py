"""
Тапалка для друзей в Telegram (рофл-версия «хомяка»).

Установка:   pip install python-telegram-bot
Запуск:      python tap_bot.py
Токен берётся у @BotFather (команда /newbot).
"""

import asyncio
import os
import sqlite3
import time

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
)

TOKEN = os.environ.get("BOT_TOKEN", "")  # или вставь токен сюда в кавычках
DB_FILE = os.path.join(os.path.expanduser("~"), "tap_bot.db")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")  # ссылка на мини-приложение
EDIT_DELAY = 0.7  # как часто обновляем сообщение (Telegram не любит чаще)

db = sqlite3.connect(DB_FILE)
db.execute(
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT,
        coins INTEGER DEFAULT 0,
        per_tap INTEGER DEFAULT 1,
        per_sec INTEGER DEFAULT 0,
        updated REAL
    )"""
)
db.commit()

pending = set()  # сообщения, у которых уже запланировано обновление


def tap_cost(u):
    return int(50 * 1.5 ** (u["per_tap"] - 1))


def auto_cost(u):
    return int(100 * 1.6 ** u["per_sec"])


def get_user(tg_user):
    """Берёт игрока из базы (создаёт нового) и начисляет пассивный доход."""
    name = tg_user.full_name or tg_user.username or str(tg_user.id)
    now = time.time()
    row = db.execute(
        "SELECT coins, per_tap, per_sec, updated FROM users WHERE id=?",
        (tg_user.id,),
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO users (id, name, updated) VALUES (?, ?, ?)",
            (tg_user.id, name, now),
        )
        row = (0, 1, 0, now)
    coins, per_tap, per_sec, updated = row
    passed = int(now - updated)
    if passed > 0:
        coins += passed * per_sec
        updated += passed
    user = {
        "id": tg_user.id, "name": name, "coins": coins,
        "per_tap": per_tap, "per_sec": per_sec, "updated": updated,
    }
    save_user(user)
    return user


def save_user(u):
    db.execute(
        "UPDATE users SET name=?, coins=?, per_tap=?, per_sec=?, updated=? "
        "WHERE id=?",
        (u["name"], u["coins"], u["per_tap"], u["per_sec"], u["updated"],
         u["id"]),
    )
    db.commit()


def panel_text(u):
    return (
        "🐯 *ТАПАЛКА*\n\n"
        "💰 Монеты: *%d*\n"
        "👆 За тап: %d\n"
        "⏱ В секунду: %d"
        % (u["coins"], u["per_tap"], u["per_sec"])
    )


def keyboard(u):
    uid = u["id"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🐯 ТАП!", callback_data="tap:%d" % uid)],
        [InlineKeyboardButton(
            "Сильнее тап +1 (%d)" % tap_cost(u),
            callback_data="uptap:%d" % uid)],
        [InlineKeyboardButton(
            "Автодоход +1/сек (%d)" % auto_cost(u),
            callback_data="upauto:%d" % uid)],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if WEBAPP_URL:
        await update.message.reply_text(
            "🐯 Жми кнопку и тапай!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "🐯 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]]),
        )
        return
    u = get_user(update.effective_user)
    await update.message.reply_text(
        panel_text(u), reply_markup=keyboard(u), parse_mode="Markdown"
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.execute(
        "SELECT name, coins FROM users ORDER BY coins DESC LIMIT 10"
    ).fetchall()
    if not rows:
        await update.message.reply_text("Пока никто не играл. Жми /start")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *Топ тапальщиков*\n"]
    for i, (name, coins) in enumerate(rows):
        mark = medals[i] if i < 3 else "%d." % (i + 1)
        lines.append("%s %s — %d" % (mark, name, coins))
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def delayed_refresh(query, key):
    """Обновляем сообщение не чаще раза в EDIT_DELAY секунд."""
    await asyncio.sleep(EDIT_DELAY)
    pending.discard(key)
    u = get_user(query.from_user)
    try:
        await query.edit_message_text(
            panel_text(u), reply_markup=keyboard(u), parse_mode="Markdown"
        )
    except TelegramError:
        pass  # например «сообщение не изменилось» — не страшно


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, _, owner = query.data.partition(":")

    if str(query.from_user.id) != owner:
        await query.answer("Это чужая тапалка. Напиши боту /start", show_alert=True)
        return

    u = get_user(query.from_user)
    alert = None

    if action == "tap":
        u["coins"] += u["per_tap"]
    elif action == "uptap":
        if u["coins"] >= tap_cost(u):
            u["coins"] -= tap_cost(u)
            u["per_tap"] += 1
        else:
            alert = "Не хватает монет!"
    elif action == "upauto":
        if u["coins"] >= auto_cost(u):
            u["coins"] -= auto_cost(u)
            u["per_sec"] += 1
        else:
            alert = "Не хватает монет!"

    save_user(u)
    await query.answer(alert)  # убирает «часики» на кнопке

    key = (query.message.chat_id, query.message.message_id)
    if key not in pending:
        pending.add(key)
        context.application.create_task(delayed_refresh(query, key))


def main():
    global WEBAPP_URL
    token = TOKEN or input("Вставь токен бота от @BotFather: ").strip()
    if not WEBAPP_URL:
        WEBAPP_URL = input(
            "Ссылка на игру https://... (Enter - простая версия на кнопках): "
        ).strip()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CallbackQueryHandler(on_button))
    print("Бот запущен. Остановить: Ctrl+C")
    app.run_polling()


if __name__ == "__main__":
    main()
