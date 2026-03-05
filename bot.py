#!/usr/bin/env python3
import json, os, re, logging
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = "prices.json"
CHECK_INTERVAL = 3600

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

WAITING_URL, WAITING_SELECTOR, WAITING_NAME = range(3)

# ─── النصوص بالعربي والإنجليزي ───
T = {
    "ar": {
        "welcome": "👋 أهلاً! اختار لغتك:",
        "main_menu": "🏠 *القائمة الرئيسية*\nاختار من الأزرار أدناه:",
        "add": "➕ إضافة منتج",
        "list": "📋 منتجاتي",
        "check": "🔍 فحص الأسعار",
        "delete": "🗑️ حذف منتج",
        "language": "🌐 تغيير اللغة",
        "send_url": "🔗 أرسل رابط المنتج:",
        "send_selector": "🎯 أرسل CSS Selector للسعر:\n\nمثال: `span.price`\n\nإزاي تجيبه؟\n• كليك يمين على السعر في الموقع\n• Inspect\n• كليك يمين على العنصر\n• Copy → Copy selector",
        "send_name": "✏️ اكتب اسم للمنتج:",
        "checking": "⏳ جاري فحص السعر...",
        "added": "✅ *تمت الإضافة!*\n\n📦 {name}\n💰 السعر الحالي: *{price}*\n\nهتاخد إشعار لما السعر يتغير 🔔",
        "error_url": "❌ الرابط غلط! لازم يبدأ بـ https://",
        "error_price": "❌ مقدرتش أجيب السعر!\nتأكد إن الـ Selector صح والموقع شغال.",
        "no_products": "📋 مفيش منتجات متابَعة دلوقتي.",
        "products_title": "📋 *منتجاتي:*\n\n",
        "no_change": "✅ مفيش تغييرات في الأسعار دلوقتي",
        "price_changed": "🔔 *تغيّر السعر!*\n\n📦 {name}\n❌ كان: *{old}*\n✅ بقى: *{new}*\n\n🔗 {url}",
        "deleted": "✅ تم حذف *{name}*",
        "select_delete": "اختار المنتج اللي عايز تحذفه:",
        "cancel": "❌ تم الإلغاء",
        "checking_all": "⏳ جاري فحص كل الأسعار...",
        "lang_changed": "✅ تم تغيير اللغة للعربية 🇦🇪",
    },
    "en": {
        "welcome": "👋 Welcome! Choose your language:",
        "main_menu": "🏠 *Main Menu*\nChoose from the buttons below:",
        "add": "➕ Add Product",
        "list": "📋 My Products",
        "check": "🔍 Check Prices",
        "delete": "🗑️ Delete Product",
        "language": "🌐 Change Language",
        "send_url": "🔗 Send the product URL:",
        "send_selector": "🎯 Send the CSS Selector for the price:\n\nExample: `span.price`\n\nHow to get it?\n• Right-click on the price\n• Inspect\n• Right-click the element\n• Copy → Copy selector",
        "send_name": "✏️ Enter a name for this product:",
        "checking": "⏳ Checking price...",
        "added": "✅ *Product Added!*\n\n📦 {name}\n💰 Current Price: *{price}*\n\nYou'll get notified when price changes 🔔",
        "error_url": "❌ Invalid URL! Must start with https://",
        "error_price": "❌ Couldn't fetch the price!\nMake sure the Selector is correct.",
        "no_products": "📋 No products tracked yet.",
        "products_title": "📋 *My Products:*\n\n",
        "no_change": "✅ No price changes right now",
        "price_changed": "🔔 *Price Changed!*\n\n📦 {name}\n❌ Was: *{old}*\n✅ Now: *{new}*\n\n🔗 {url}",
        "deleted": "✅ Deleted *{name}*",
        "select_delete": "Choose a product to delete:",
        "cancel": "❌ Cancelled",
        "checking_all": "⏳ Checking all prices...",
        "lang_changed": "✅ Language changed to English 🇬🇧",
    }
}

# ─── البيانات ───
def load_data():
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_lang(chat_id):
    data = load_data()
    return data.get(str(chat_id), {}).get("lang", None)

def set_lang(chat_id, lang):
    data = load_data()
    cid = str(chat_id)
    if cid not in data:
        data[cid] = {}
    data[cid]["lang"] = lang
    save_data(data)

def t(chat_id, key, **kwargs):
    lang = get_lang(chat_id) or "ar"
    text = T[lang].get(key, key)
    return text.format(**kwargs) if kwargs else text

# ─── الكيبورد الرئيسي ───
def main_keyboard(chat_id):
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]
    keyboard = [
        [KeyboardButton(txt["add"]), KeyboardButton(txt["list"])],
        [KeyboardButton(txt["check"]), KeyboardButton(txt["delete"])],
        [KeyboardButton(txt["language"])]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ─── جلب السعر ───
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ar,en;q=0.9",
}

async def fetch_price(url, selector):
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.select_one(selector)
        if el:
            text = el.get_text().strip()
            match = re.search(r"[\d,،.]+", text.replace("\xa0", " "))
            return match.group(0) if match else text[:50]
    except Exception as e:
        logger.warning(f"fetch_price error: {e}")
    return None

# ─── اختيار اللغة ───
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lang = get_lang(chat_id)
    if lang:
        await update.message.reply_text(
            t(chat_id, "main_menu"),
            parse_mode="Markdown",
            reply_markup=main_keyboard(chat_id)
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇦🇪 عربي", callback_data="lang_ar"),
             InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
        ])
        await update.message.reply_text("👋 أهلاً! / Welcome!\nاختار لغتك / Choose language:", reply_markup=keyboard)

async def lang_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    lang = query.data.replace("lang_", "")
    set_lang(chat_id, lang)
    await query.edit_message_text("✅ " + ("تم اختيار العربية 🇦🇪" if lang == "ar" else "English selected 🇬🇧"))
    await ctx.bot.send_message(
        chat_id=chat_id,
        text=t(chat_id, "main_menu"),
        parse_mode="Markdown",
        reply_markup=main_keyboard(chat_id)
    )

# ─── إضافة منتج ───
async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "send_url"))
    return WAITING_URL

async def add_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text(t(chat_id, "error_url"))
        return WAITING_URL
    ctx.user_data["url"] = url
    await update.message.reply_text(t(chat_id, "send_selector"), parse_mode="Markdown")
    return WAITING_SELECTOR

async def add_selector(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx.user_data["selector"] = update.message.text.strip()
    await update.message.reply_text(t(chat_id, "send_name"))
    return WAITING_NAME

async def add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    name = update.message.text.strip()
    url = ctx.user_data["url"]
    selector = ctx.user_data["selector"]

    await update.message.reply_text(t(int(chat_id), "checking"))
    price = await fetch_price(url, selector)

    if not price:
        await update.message.reply_text(t(int(chat_id), "error_price"))
        return ConversationHandler.END

    data = load_data()
    if chat_id not in data:
        data[chat_id] = {}
    if "products" not in data[chat_id]:
        data[chat_id]["products"] = {}

    pid = str(len(data[chat_id]["products"]) + 1)
    data[chat_id]["products"][pid] = {
        "url": url, "selector": selector,
        "price": price, "name": name,
        "added": datetime.now().isoformat()
    }
    save_data(data)

    await update.message.reply_text(
        t(int(chat_id), "added", name=name, price=price),
        parse_mode="Markdown",
        reply_markup=main_keyboard(int(chat_id))
    )
    return ConversationHandler.END

async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "cancel"), reply_markup=main_keyboard(chat_id))
    return ConversationHandler.END

# ─── عرض المنتجات ───
async def list_products(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})

    if not products:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return

    text = t(int(chat_id), "products_title")
    for pid, p in products.items():
        text += f"*{pid}.* {p['name']}\n   💰 {p['price']}\n   🔗 {p['url'][:45]}...\n\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(int(chat_id)))

# ─── فحص الأسعار ───
async def check_prices(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})

    if not products:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return

    await update.message.reply_text(t(int(chat_id), "checking_all"))
    changed = 0

    for pid, p in products.items():
        new_price = await fetch_price(p["url"], p["selector"])
        if new_price and new_price != p["price"]:
            old = p["price"]
            data[chat_id]["products"][pid]["price"] = new_price
            changed += 1
            await update.message.reply_text(
                t(int(chat_id), "price_changed", name=p["name"], old=old, new=new_price, url=p["url"]),
                parse_mode="Markdown"
            )

    save_data(data)
    if changed == 0:
        await update.message.reply_text(t(int(chat_id), "no_change"), reply_markup=main_keyboard(int(chat_id)))

# ─── حذف منتج ───
async def delete_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})

    if not products:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return

    keyboard = [
        [InlineKeyboardButton(f"🗑️ {p['name']} — {p['price']}", callback_data=f"del_{pid}")]
        for pid, p in products.items()
    ]
    await update.message.reply_text(
        t(int(chat_id), "select_delete"),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    pid = query.data.replace("del_", "")
    data = load_data()

    if chat_id in data and pid in data[chat_id].get("products", {}):
        name = data[chat_id]["products"][pid]["name"]
        del data[chat_id]["products"][pid]
        save_data(data)
        await query.edit_message_text(t(int(chat_id), "deleted", name=name), parse_mode="Markdown")
    
    await ctx.bot.send_message(
        chat_id=int(chat_id),
        text=t(int(chat_id), "main_menu"),
        parse_mode="Markdown",
        reply_markup=main_keyboard(int(chat_id))
    )

# ─── تغيير اللغة ───
async def change_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇦🇪 عربي", callback_data="lang_ar"),
         InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ])
    await update.message.reply_text("اختار اللغة / Choose language:", reply_markup=keyboard)

# ─── معالج الأزرار النصية ───
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]

    if text == txt["add"]:
        await update.message.reply_text(t(chat_id, "send_url"))
        ctx.user_data["state"] = "url"
    elif text == txt["list"]:
        await list_products(update, ctx)
    elif text == txt["check"]:
        await check_prices(update, ctx)
    elif text == txt["delete"]:
        await delete_product(update, ctx)
    elif text == txt["language"]:
        await change_language(update, ctx)

# ─── الفحص التلقائي ───
async def auto_check(ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    for chat_id, user_data in data.items():
        products = user_data.get("products", {})
        for pid, p in products.items():
            new_price = await fetch_price(p["url"], p["selector"])
            if new_price and new_price != p["price"]:
                old = p["price"]
                data[chat_id]["products"][pid]["price"] = new_price
                try:
                    await ctx.bot.send_message(
                        chat_id=int(chat_id),
                        text=t(int(chat_id), "price_changed", name=p["name"], old=old, new=new_price, url=p["url"]),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"send error: {e}")
    save_data(data)

# ─── تشغيل البوت ───
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
        ],
        states={
            WAITING_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
            WAITING_SELECTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_selector)],
            WAITING_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del_"))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    app.job_queue.run_repeating(auto_check, interval=CHECK_INTERVAL, first=60)

    logger.info("✅ البوت شغال!")
    app.run_polling()

if __name__ == "__main__":
    main()
