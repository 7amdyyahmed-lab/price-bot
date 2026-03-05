#!/usr/bin/env python3
import asyncio
import json
import os
import re
import logging
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE  = "prices.json"
CHECK_INTERVAL = 3600

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WAITING_URL, WAITING_SELECTOR = range(2)

def load_data() -> dict:
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
}

def extract_price(text: str) -> str | None:
    text = text.strip()
    match = re.search(r"[\d,،.]+", text.replace("\xa0", " "))
    return match.group(0).replace("،", ",") if match else text[:50]

async def fetch_price(url: str, selector: str) -> str | None:
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.select_one(selector)
        if el:
            return extract_price(el.get_text())
    except Exception as e:
        logger.warning(f"fetch_price error: {e}")
    return None

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 أهلاً! أنا بوت تتبع الأسعار 🛒\n\n"
        "📌 *الأوامر المتاحة:*\n"
        "/add — إضافة منتج جديد للمتابعة\n"
        "/list — عرض المنتجات المتابَعة\n"
        "/check — فحص الأسعار الآن\n"
        "/delete — حذف منتج\n"
        "/help — مساعدة\n\n"
        "ابدأ بـ /add لإضافة أول منتج! 🎯"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *كيفية الاستخدام:*\n\n"
        "1️⃣ اكتب /add\n"
        "2️⃣ أرسل رابط صفحة المنتج\n"
        "3️⃣ أرسل CSS Selector للسعر\n\n"
        "💡 *إزاي تعرف الـ Selector؟*\n"
        "• افتح الموقع في Chrome\n"
        "• كليك يمين على السعر ← Inspect\n"
        "• كليك يمين على العنصر ← Copy → Copy selector\n\n"
        "🔔 هتاخد إشعار أوتوماتيك لما السعر يتغير"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 أرسل رابط صفحة المنتج (URL كاملاً يبدأ بـ https://)"
    )
    return WAITING_URL

async def add_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ الرابط غلط، لازم يبدأ بـ https://\nحاول تاني:")
        return WAITING_URL
    ctx.user_data["pending_url"] = url
    await update.message.reply_text(
        "🎯 أرسل CSS Selector للسعر\n\n"
        "مثال: `span.price` أو `#priceblock_ourprice`\n\n"
        "اكتب /help لمعرفة إزاي تجيب الـ Selector",
        parse_mode="Markdown"
    )
    return WAITING_SELECTOR

async def add_selector(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    selector = update.message.text.strip()
    url      = ctx.user_data.get("pending_url")
    chat_id  = str(update.effective_chat.id)

    await update.message.reply_text("⏳ بفحص السعر دلوقتي...")

    price = await fetch_price(url, selector)
    if price is None:
        await update.message.reply_text(
            "❌ مقدرتش أجيب السعر!\n"
            "تأكد إن الـ Selector صح والموقع شغال.\n"
            "حاول تاني بـ /add"
        )
        return ConversationHandler.END

    data = load_data()
    if chat_id not in data:
        data[chat_id] = {}

    product_id = str(len(data[chat_id]) + 1)
    data[chat_id][product_id] = {
        "url": url,
        "selector": selector,
        "price": price,
        "name": f"منتج {product_id}",
        "added": datetime.now().isoformat(),
    }
    save_data(data)

    await update.message.reply_text(
        f"✅ *تمت الإضافة!*\n\n"
        f"🔗 {url[:60]}...\n"
        f"💰 السعر الحالي: *{price}*\n\n"
        f"هتاخد إشعار لما السعر يتغير 🔔",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء")
    return ConversationHandler.END

async def list_products(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data    = load_data()
    products = data.get(chat_id, {})

    if not products:
        await update.message.reply_text("📋 مفيش منتجات متابَعة دلوقتي.\nاستخدم /add لإضافة منتج!")
        return

    text = "📋 *المنتجات المتابَعة:*\n\n"
    for pid, p in products.items():
        text += (
            f"*{pid}.* {p['name']}\n"
            f"   💰 السعر: `{p['price']}`\n"
            f"   🔗 {p['url'][:50]}...\n\n"
        )
    await update.message.reply_text(text, parse_mode="Markdown")

async def check_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data    = load_data()
    products = data.get(chat_id, {})

    if not products:
        await update.message.reply_text("مفيش منتجات للفحص. استخدم /add أولاً")
        return

    await update.message.reply_text("⏳ جاري فحص الأسعار...")
    changed = 0

    for pid, p in products.items():
        new_price = await fetch_price(p["url"], p["selector"])
        if new_price and new_price != p["price"]:
            old = p["price"]
            data[chat_id][pid]["price"] = new_price
            changed += 1
            await update.message.reply_text(
                f"🔔 *تغيّر السعر!*\n\n"
                f"📦 {p['name']}\n"
                f"❌ السعر القديم: `{old}`\n"
                f"✅ السعر الجديد: `{new_price}`\n\n"
                f"🔗 {p['url']}",
                parse_mode="Markdown"
            )

    save_data(data)
    if changed == 0:
        await update.message.reply_text("✅ مفيش تغييرات في الأسعار دلوقتي")

async def delete_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id  = str(update.effective_chat.id)
    data     = load_data()
    products = data.get(chat_id, {})

    if not products:
        await update.message.reply_text("مفيش منتجات للحذف")
        return

    keyboard = [
        [InlineKeyboardButton(f"❌ {p['name']} ({p['price']})", callback_data=f"del_{pid}")]
        for pid, p in products.items()
    ]
    await update.message.reply_text(
        "اختار المنتج اللي عايز تحذفه:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = str(query.message.chat_id)
    pid     = query.data.replace("del_", "")

    data = load_data()
    if chat_id in data and pid in data[chat_id]:
        name = data[chat_id][pid]["name"]
        del data[chat_id][pid]
        save_data(data)
        await query.edit_message_text(f"✅ تم حذف *{name}*", parse_mode="Markdown")
    else:
        await query.edit_message_text("❌ المنتج مش موجود")

async def auto_check(ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    for chat_id, products in data.items():
        for pid, p in products.items():
            new_price = await fetch_price(p["url"], p["selector"])
            if new_price and new_price != p["price"]:
                old = p["price"]
                data[chat_id][pid]["price"] = new_price
                try:
                    await ctx.bot.send_message(
                        chat_id=int(chat_id),
                        text=(
                            f"🔔 *تغيّر السعر!*\n\n"
                            f"📦 {p['name']}\n"
                            f"❌ كان: `{old}`\n"
                            f"✅ بقى: `{new_price}`\n\n"
                            f"🔗 {p['url']}"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"send error: {e}")
    save_data(data)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            WAITING_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
            WAITING_SELECTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_selector)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_cmd))
    app.add_handler(CommandHandler("list",   list_products))
    app.add_handler(CommandHandler("check",  check_now))
    app.add_handler(CommandHandler("delete", delete_product))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del_"))
    app.add_handler(conv)

    app.job_queue.run_repeating(auto_check, interval=CHECK_INTERVAL, first=60)

    logger.info("✅ البوت شغال!")
    app.run_polling()

if __name__ == "__main__":
    main()
