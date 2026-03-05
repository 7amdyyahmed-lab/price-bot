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

# ─── النصوص ───
T = {
    "ar": {
        "choose_lang": "👋 أهلاً *{name}*!\nاختار لغتك:",
        "welcome": (
            "🎉 *أهلاً وسهلاً {name}!*\n\n"
            "أنا بوت تتبع الأسعار 🛒\n"
            "هساعدك تتابع أسعار أي منتج على الإنترنت\n"
            "وتاخد إشعار فوري لما السعر يتغير! 🔔\n\n"
            "━━━━━━━━━━━━━━\n"
            "📌 *إيه اللي أقدر أعمله؟*\n\n"
            "➕ *إضافة منتج* — أضيف أي منتج من أي موقع\n"
            "📋 *منتجاتي* — أشوف كل المنتجات المتابَعة\n"
            "🔍 *فحص الأسعار* — أتحقق من الأسعار دلوقتي\n"
            "🗑️ *حذف منتج* — أمسح منتج من القائمة\n"
            "📊 *إحصائياتي* — أشوف إحصائيات حسابي\n"
            "❓ *مساعدة* — دليل الاستخدام\n\n"
            "━━━━━━━━━━━━━━\n"
            "اضغط على أي زرار للبدء! 👇"
        ),
        "main_menu": "🏠 *القائمة الرئيسية*\nاختار من الأزرار أدناه:",
        "add": "➕ إضافة منتج",
        "list": "📋 منتجاتي",
        "check": "🔍 فحص الأسعار",
        "delete": "🗑️ حذف منتج",
        "language": "🌐 تغيير اللغة",
        "stats": "📊 إحصائياتي",
        "help": "❓ مساعدة",
        "send_url": "🔗 أرسل رابط المنتج:\n\nمثال: https://example.com/product",
        "send_selector": (
            "🎯 أرسل CSS Selector للسعر:\n\n"
            "مثال: `span.price`\n\n"
            "📖 *إزاي تجيب الـ Selector؟*\n"
            "1️⃣ افتح الموقع في Chrome\n"
            "2️⃣ كليك يمين على *السعر*\n"
            "3️⃣ اختار Inspect\n"
            "4️⃣ كليك يمين على العنصر الأزرق\n"
            "5️⃣ Copy ← Copy selector"
        ),
        "send_name": "✏️ اكتب اسم للمنتج عشان تعرفه بسهولة:",
        "checking": "⏳ جاري فحص السعر...",
        "added": (
            "✅ *تمت الإضافة بنجاح!*\n\n"
            "📦 *{name}*\n"
            "💰 السعر الحالي: *{price}*\n"
            "🕐 تمت الإضافة: {time}\n\n"
            "🔔 هتاخد إشعار فوري لما السعر يتغير!"
        ),
        "error_url": "❌ الرابط غلط! لازم يبدأ بـ https://\nحاول تاني:",
        "error_price": (
            "❌ *مقدرتش أجيب السعر!*\n\n"
            "ممكن يكون السبب:\n"
            "• الـ Selector غلط\n"
            "• الموقع مش شغال\n"
            "• الموقع بيمنع السكرابينج\n\n"
            "حاول تاني بـ /add"
        ),
        "no_products": "📋 مفيش منتجات متابَعة دلوقتي.\n\nاضغط ➕ إضافة منتج للبدء!",
        "products_title": "📋 *منتجاتي:*\n\n",
        "no_change": "✅ مفيش تغييرات في الأسعار دلوقتي\n\nآخر فحص: {time}",
        "price_changed": (
            "🔔 *تغيّر السعر!*\n\n"
            "📦 *{name}*\n"
            "❌ السعر القديم: *{old}*\n"
            "✅ السعر الجديد: *{new}*\n\n"
            "🔗 {url}"
        ),
        "price_down": "📉 *انخفض السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}* 🎉\n\n🔗 {url}",
        "price_up": "📈 *ارتفع السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n\n🔗 {url}",
        "deleted": "✅ تم حذف *{name}* بنجاح",
        "select_delete": "🗑️ اختار المنتج اللي عايز تحذفه:",
        "cancel": "❌ تم الإلغاء\n\nرجعت للقائمة الرئيسية 🏠",
        "checking_all": "⏳ جاري فحص كل الأسعار...\nاستنى لحظة!",
        "lang_changed": "✅ تم تغيير اللغة للعربية 🇪🇬",
        "stats_text": (
            "📊 *إحصائياتك:*\n\n"
            "📦 عدد المنتجات المتابَعة: *{count}*\n"
            "📅 تاريخ أول منتج: *{first}*\n"
            "🕐 آخر فحص للأسعار: *{last_check}*\n\n"
            "استمر في المتابعة! 💪"
        ),
        "help_text": (
            "❓ *دليل الاستخدام:*\n\n"
            "━━━━━━━━━━━━━━\n"
            "➕ *إضافة منتج:*\n"
            "1. اضغط إضافة منتج\n"
            "2. ابعت رابط المنتج\n"
            "3. ابعت CSS Selector للسعر\n"
            "4. اكتب اسم للمنتج\n\n"
            "━━━━━━━━━━━━━━\n"
            "🎯 *إزاي تجيب الـ Selector؟*\n"
            "1. افتح الموقع في Chrome\n"
            "2. كليك يمين على السعر\n"
            "3. اختار Inspect\n"
            "4. كليك يمين على العنصر\n"
            "5. Copy ← Copy selector\n\n"
            "━━━━━━━━━━━━━━\n"
            "⏰ *البوت بيفحص الأسعار كل ساعة تلقائياً*\n\n"
            "📞 *محتاج مساعدة؟* تواصل مع المطور"
        ),
    },
    "en": {
        "choose_lang": "👋 Hello *{name}*!\nChoose your language:",
        "welcome": (
            "🎉 *Welcome {name}!*\n\n"
            "I'm your Price Tracker Bot 🛒\n"
            "I'll help you track any product price\n"
            "and notify you instantly when it changes! 🔔\n\n"
            "━━━━━━━━━━━━━━\n"
            "📌 *What can I do?*\n\n"
            "➕ *Add Product* — Track any product\n"
            "📋 *My Products* — View tracked products\n"
            "🔍 *Check Prices* — Check prices now\n"
            "🗑️ *Delete Product* — Remove a product\n"
            "📊 *My Stats* — View your statistics\n"
            "❓ *Help* — Usage guide\n\n"
            "━━━━━━━━━━━━━━\n"
            "Tap any button to start! 👇"
        ),
        "main_menu": "🏠 *Main Menu*\nChoose from the buttons below:",
        "add": "➕ Add Product",
        "list": "📋 My Products",
        "check": "🔍 Check Prices",
        "delete": "🗑️ Delete Product",
        "language": "🌐 Change Language",
        "stats": "📊 My Stats",
        "help": "❓ Help",
        "send_url": "🔗 Send the product URL:\n\nExample: https://example.com/product",
        "send_selector": (
            "🎯 Send the CSS Selector for the price:\n\n"
            "Example: `span.price`\n\n"
            "📖 *How to get the Selector?*\n"
            "1️⃣ Open the website in Chrome\n"
            "2️⃣ Right-click on the *price*\n"
            "3️⃣ Click Inspect\n"
            "4️⃣ Right-click the blue element\n"
            "5️⃣ Copy ← Copy selector"
        ),
        "send_name": "✏️ Enter a name for this product:",
        "checking": "⏳ Checking price...",
        "added": (
            "✅ *Product Added Successfully!*\n\n"
            "📦 *{name}*\n"
            "💰 Current Price: *{price}*\n"
            "🕐 Added: {time}\n\n"
            "🔔 You'll be notified instantly when price changes!"
        ),
        "error_url": "❌ Invalid URL! Must start with https://\nTry again:",
        "error_price": (
            "❌ *Couldn't fetch the price!*\n\n"
            "Possible reasons:\n"
            "• Wrong Selector\n"
            "• Website is down\n"
            "• Website blocks scraping\n\n"
            "Try again with /add"
        ),
        "no_products": "📋 No products tracked yet.\n\nPress ➕ Add Product to start!",
        "products_title": "📋 *My Products:*\n\n",
        "no_change": "✅ No price changes right now\n\nLast check: {time}",
        "price_changed": (
            "🔔 *Price Changed!*\n\n"
            "📦 *{name}*\n"
            "❌ Old Price: *{old}*\n"
            "✅ New Price: *{new}*\n\n"
            "🔗 {url}"
        ),
        "price_down": "📉 *Price Dropped!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}* 🎉\n\n🔗 {url}",
        "price_up": "📈 *Price Increased!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n\n🔗 {url}",
        "deleted": "✅ *{name}* deleted successfully",
        "select_delete": "🗑️ Choose a product to delete:",
        "cancel": "❌ Cancelled\n\nBack to main menu 🏠",
        "checking_all": "⏳ Checking all prices...\nPlease wait!",
        "lang_changed": "✅ Language changed to English 🇬🇧",
        "stats_text": (
            "📊 *Your Statistics:*\n\n"
            "📦 Tracked Products: *{count}*\n"
            "📅 First Product Added: *{first}*\n"
            "🕐 Last Price Check: *{last_check}*\n\n"
            "Keep tracking! 💪"
        ),
        "help_text": (
            "❓ *Usage Guide:*\n\n"
            "━━━━━━━━━━━━━━\n"
            "➕ *Add Product:*\n"
            "1. Press Add Product\n"
            "2. Send the product URL\n"
            "3. Send the CSS Selector\n"
            "4. Enter a product name\n\n"
            "━━━━━━━━━━━━━━\n"
            "🎯 *How to get the Selector?*\n"
            "1. Open website in Chrome\n"
            "2. Right-click on the price\n"
            "3. Click Inspect\n"
            "4. Right-click the element\n"
            "5. Copy ← Copy selector\n\n"
            "━━━━━━━━━━━━━━\n"
            "⏰ *Bot checks prices every hour automatically*\n\n"
            "📞 *Need help?* Contact the developer"
        ),
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

def get_name(user):
    if user.first_name:
        return user.first_name
    return user.username or "صديقي"

# ─── الكيبورد الرئيسي ───
def main_keyboard(chat_id):
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]
    keyboard = [
        [KeyboardButton(txt["add"]), KeyboardButton(txt["list"])],
        [KeyboardButton(txt["check"]), KeyboardButton(txt["delete"])],
        [KeyboardButton(txt["stats"]), KeyboardButton(txt["help"])],
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

def price_to_float(price_str):
    try:
        return float(re.sub(r"[^\d.]", "", price_str.replace(",", ".")))
    except:
        return None

# ─── Start ───
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = get_name(update.effective_user)
    lang = get_lang(chat_id)

    if lang:
        await update.message.reply_text(
            t(chat_id, "welcome", name=name),
            parse_mode="Markdown",
            reply_markup=main_keyboard(chat_id)
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇪🇬 عربي", callback_data="lang_ar"),
             InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
        ])
        await update.message.reply_text(
            f"👋 أهلاً *{name}*! / Welcome *{name}*!\nاختار لغتك / Choose language:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

async def lang_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    name = get_name(query.from_user)
    lang = query.data.replace("lang_", "")
    set_lang(chat_id, lang)
    await query.edit_message_text("✅ " + ("تم اختيار العربية 🇪🇬" if lang == "ar" else "English selected 🇬🇧"))
    await ctx.bot.send_message(
        chat_id=chat_id,
        text=T[lang]["welcome"].format(name=name),
        parse_mode="Markdown",
        reply_markup=main_keyboard(chat_id)
    )

# ─── إضافة منتج ───
async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "send_url"), parse_mode="Markdown")
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
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    await update.message.reply_text(t(int(chat_id), "checking"))
    price = await fetch_price(url, selector)

    if not price:
        await update.message.reply_text(t(int(chat_id), "error_price"), parse_mode="Markdown")
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
        "added": now, "last_check": now
    }
    if "last_check" not in data[chat_id]:
        data[chat_id]["last_check"] = now
    save_data(data)

    await update.message.reply_text(
        t(int(chat_id), "added", name=name, price=price, time=now),
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
        text += f"*{pid}.* 📦 {p['name']}\n   💰 السعر: *{p['price']}*\n   📅 أضيف: {p.get('added','—')}\n   🔗 {p['url'][:45]}...\n\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(int(chat_id)))

# ─── فحص الأسعار ───
async def check_prices(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

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
            data[chat_id]["products"][pid]["last_check"] = now
            changed += 1

            old_f = price_to_float(old)
            new_f = price_to_float(new_price)
            if old_f and new_f:
                key = "price_down" if new_f < old_f else "price_up"
            else:
                key = "price_changed"

            await update.message.reply_text(
                t(int(chat_id), key, name=p["name"], old=old, new=new_price, url=p["url"]),
                parse_mode="Markdown"
            )

    data[chat_id]["last_check"] = now
    save_data(data)

    if changed == 0:
        await update.message.reply_text(
            t(int(chat_id), "no_change", time=now),
            reply_markup=main_keyboard(int(chat_id))
        )

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

# ─── إحصائيات ───
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    user_data = data.get(chat_id, {})
    products = user_data.get("products", {})
    count = len(products)
    first = min((p.get("added", "—") for p in products.values()), default="—")
    last_check = user_data.get("last_check", "—")

    await update.message.reply_text(
        t(int(chat_id), "stats_text", count=count, first=first, last_check=last_check),
        parse_mode="Markdown",
        reply_markup=main_keyboard(int(chat_id))
    )

# ─── مساعدة ───
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        t(chat_id, "help_text"),
        parse_mode="Markdown",
        reply_markup=main_keyboard(chat_id)
    )

# ─── تغيير اللغة ───
async def change_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇪🇬 عربي", callback_data="lang_ar"),
         InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ])
    await update.message.reply_text("اختار اللغة / Choose language:", reply_markup=keyboard)

# ─── معالج الأزرار ───
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]

    if text == txt["list"]:
        await list_products(update, ctx)
    elif text == txt["check"]:
        await check_prices(update, ctx)
    elif text == txt["delete"]:
        await delete_product(update, ctx)
    elif text == txt["language"]:
        await change_language(update, ctx)
    elif text == txt["stats"]:
        await stats(update, ctx)
    elif text == txt["help"]:
        await help_cmd(update, ctx)

# ─── الفحص التلقائي ───
async def auto_check(ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for chat_id, user_data in data.items():
        products = user_data.get("products", {})
        for pid, p in products.items():
            new_price = await fetch_price(p["url"], p["selector"])
            if new_price and new_price != p["price"]:
                old = p["price"]
                data[chat_id]["products"][pid]["price"] = new_price
                data[chat_id]["products"][pid]["last_check"] = now

                old_f = price_to_float(old)
                new_f = price_to_float(new_price)
                if old_f and new_f:
                    key = "price_down" if new_f < old_f else "price_up"
                else:
                    key = "price_changed"

                try:
                    await ctx.bot.send_message(
                        chat_id=int(chat_id),
                        text=t(int(chat_id), key, name=p["name"], old=old, new=new_price, url=p["url"]),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"send error: {e}")
        data[chat_id]["last_check"] = now
    save_data(data)

# ─── تشغيل البوت ───
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(r"^(➕ إضافة منتج|➕ Add Product)$"), add_start),
        ],
        states={
            WAITING_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
            WAITING_SELECTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_selector)],
            WAITING_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del_"))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    app.job_queue.run_repeating(auto_check, interval=CHECK_INTERVAL, first=60)

    logger.info("✅ البوت شغال!")
    app.run_polling()

if __name__ == "__main__":
    main()
