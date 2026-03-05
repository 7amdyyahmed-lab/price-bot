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

# مراحل إضافة منتج
(WAITING_URL, WAITING_SELECTOR, WAITING_NAME,
 WAITING_ALERT_TYPE, WAITING_ALERT_VALUE) = range(5)

# مراحل مراقبة موقع
WAITING_SITE_URL, WAITING_SITE_SELECTOR, WAITING_SITE_NAME = range(5, 8)

# ─── النصوص ───
T = {
    "ar": {
        "choose_lang": "👋 أهلاً *{name}*!\nاختار لغتك:",
        "welcome": (
            "🎉 *أهلاً وسهلاً {name}!*\n\n"
            "أنا بوت تتبع الأسعار الذكي 🛒\n\n"
            "━━━━━━━━━━━━━━\n"
            "📌 *إيه اللي أقدر أعمله؟*\n\n"
            "➕ *إضافة منتج* — تابع سعر أي منتج\n"
            "🌐 *مراقبة موقع* — راقب صفحة عروض كاملة\n"
            "📋 *منتجاتي* — شوف كل المنتجات\n"
            "🔍 *فحص الأسعار* — تحقق دلوقتي\n"
            "🗑️ *حذف منتج* — امسح منتج\n"
            "📊 *إحصائياتي* — إحصائيات حسابك\n"
            "❓ *مساعدة* — دليل الاستخدام\n\n"
            "━━━━━━━━━━━━━━\n"
            "اضغط على أي زرار للبدء! 👇"
        ),
        "main_menu": "🏠 *القائمة الرئيسية*",
        "add": "➕ إضافة منتج",
        "watch_site": "🌐 مراقبة موقع",
        "list": "📋 منتجاتي",
        "check": "🔍 فحص الأسعار",
        "delete": "🗑️ حذف",
        "language": "🌐 تغيير اللغة",
        "stats": "📊 إحصائياتي",
        "help": "❓ مساعدة",
        "send_url": "🔗 أرسل رابط المنتج:",
        "send_selector": (
            "🎯 أرسل CSS Selector للسعر:\n\n"
            "مثال: `span.price`\n\n"
            "📖 *إزاي تجيبه؟*\n"
            "1️⃣ افتح الموقع في Chrome\n"
            "2️⃣ كليك يمين على *السعر*\n"
            "3️⃣ Inspect\n"
            "4️⃣ كليك يمين على العنصر\n"
            "5️⃣ Copy ← Copy selector"
        ),
        "send_name": "✏️ اكتب اسم للمنتج:",
        "choose_alert": (
            "🔔 *اختار نوع التنبيه:*\n\n"
            "1️⃣ أي تغيير في السعر\n"
            "2️⃣ لما ينزل بنسبة معينة\n"
            "3️⃣ لما يوصل سعر معين"
        ),
        "alert_any": "🔔 أي تغيير",
        "alert_percent": "📉 نسبة خصم",
        "alert_target": "🎯 سعر معين",
        "send_percent": "📉 اكتب نسبة الخصم اللي عايز تتنبه ليها:\n\nمثال: اكتب `30` يعني لما السعر ينزل 30% أو أكتر",
        "send_target": "🎯 اكتب السعر اللي عايز توصله:\n\nمثال: اكتب `100` يعني لما السعر يبقى 100 أو أقل",
        "checking": "⏳ جاري فحص السعر...",
        "added": (
            "✅ *تمت الإضافة!*\n\n"
            "📦 *{name}*\n"
            "💰 السعر الحالي: *{price}*\n"
            "🔔 نوع التنبيه: *{alert}*\n"
            "🕐 {time}\n\n"
            "هتاخد إشعار لما الشرط يتحقق! 🎯"
        ),
        "alert_desc_any": "أي تغيير في السعر",
        "alert_desc_percent": "لما ينزل {val}% أو أكتر",
        "alert_desc_target": "لما يوصل {val} أو أقل",
        "error_url": "❌ الرابط غلط! لازم يبدأ بـ https://",
        "error_price": "❌ مقدرتش أجيب السعر!\nتأكد إن الـ Selector صح.",
        "error_number": "❌ لازم تكتب رقم صح!\nحاول تاني:",
        "no_products": "📋 مفيش منتجات متابَعة دلوقتي.",
        "products_title": "📋 *منتجاتي:*\n\n",
        "no_change": "✅ مفيش تغييرات\nآخر فحص: {time}",
        "price_down": "📉 *انخفض السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n💰 خصم: *{pct}%* 🎉\n\n🔗 {url}",
        "price_up": "📈 *ارتفع السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n\n🔗 {url}",
        "price_target": "🎯 *وصل للسعر المطلوب!*\n\n📦 *{name}*\n✅ السعر دلوقتي: *{new}*\n\n🔗 {url}",
        "deleted": "✅ تم الحذف بنجاح",
        "select_delete": "🗑️ اختار اللي عايز تحذفه:",
        "cancel": "❌ تم الإلغاء",
        "checking_all": "⏳ جاري فحص كل الأسعار...",
        "lang_changed": "✅ تم تغيير اللغة 🇪🇬",
        "stats_text": (
            "📊 *إحصائياتك:*\n\n"
            "📦 منتجات متابَعة: *{products}*\n"
            "🌐 مواقع تحت المراقبة: *{sites}*\n"
            "🕐 آخر فحص: *{last_check}*"
        ),
        "help_text": (
            "❓ *دليل الاستخدام:*\n\n"
            "━━━━━━━━━━━━━━\n"
            "➕ *إضافة منتج:*\n"
            "ابعت رابط المنتج ← Selector ← اسم ← نوع التنبيه\n\n"
            "━━━━━━━━━━━━━━\n"
            "🌐 *مراقبة موقع كامل:*\n"
            "ابعت رابط صفحة العروض ← Selector للسعر ← اسم\n"
            "البوت هيبعتلك كل المنتجات الجديدة في الصفحة\n\n"
            "━━━━━━━━━━━━━━\n"
            "⏰ الفحص كل ساعة تلقائياً"
        ),
        # مراقبة موقع
        "send_site_url": (
            "🌐 أرسل رابط صفحة العروض أو التخفيضات:\n\n"
            "مثال:\n"
            "`https://dkhoonemirates.com/collections/sale`"
        ),
        "send_site_selector": (
            "🎯 أرسل CSS Selector للسعر في الصفحة:\n\n"
            "مثال: `span.price`"
        ),
        "send_site_name": "✏️ اكتب اسم للموقع:",
        "site_added": (
            "✅ *تمت إضافة الموقع!*\n\n"
            "🌐 *{name}*\n"
            "📦 المنتجات الحالية: *{count}*\n\n"
            "هتاخد إشعار لما يظهر خصم جديد! 🔔"
        ),
        "site_new_deals": (
            "🔥 *عروض جديدة على {name}!*\n\n"
            "{deals}\n"
            "🔗 {url}"
        ),
        "no_sites": "🌐 مفيش مواقع تحت المراقبة دلوقتي.",
    },
    "en": {
        "choose_lang": "👋 Hello *{name}*!\nChoose your language:",
        "welcome": (
            "🎉 *Welcome {name}!*\n\n"
            "I'm your Smart Price Tracker Bot 🛒\n\n"
            "━━━━━━━━━━━━━━\n"
            "📌 *What can I do?*\n\n"
            "➕ *Add Product* — Track any product price\n"
            "🌐 *Watch Site* — Monitor a full deals page\n"
            "📋 *My Products* — View all tracked items\n"
            "🔍 *Check Prices* — Check now\n"
            "🗑️ *Delete* — Remove items\n"
            "📊 *My Stats* — Your statistics\n"
            "❓ *Help* — Usage guide\n\n"
            "━━━━━━━━━━━━━━\n"
            "Tap any button to start! 👇"
        ),
        "main_menu": "🏠 *Main Menu*",
        "add": "➕ Add Product",
        "watch_site": "🌐 Watch Site",
        "list": "📋 My Products",
        "check": "🔍 Check Prices",
        "delete": "🗑️ Delete",
        "language": "🌐 Language",
        "stats": "📊 My Stats",
        "help": "❓ Help",
        "send_url": "🔗 Send the product URL:",
        "send_selector": (
            "🎯 Send the CSS Selector for the price:\n\n"
            "Example: `span.price`\n\n"
            "📖 *How to get it?*\n"
            "1️⃣ Open in Chrome\n"
            "2️⃣ Right-click the price\n"
            "3️⃣ Inspect\n"
            "4️⃣ Right-click element\n"
            "5️⃣ Copy ← Copy selector"
        ),
        "send_name": "✏️ Enter a name for this product:",
        "choose_alert": (
            "🔔 *Choose alert type:*\n\n"
            "1️⃣ Any price change\n"
            "2️⃣ When drops by a percentage\n"
            "3️⃣ When reaches a target price"
        ),
        "alert_any": "🔔 Any Change",
        "alert_percent": "📉 % Discount",
        "alert_target": "🎯 Target Price",
        "send_percent": "📉 Enter the discount % to alert you:\n\nExample: `30` means when price drops 30% or more",
        "send_target": "🎯 Enter your target price:\n\nExample: `100` means when price reaches 100 or less",
        "checking": "⏳ Checking price...",
        "added": (
            "✅ *Product Added!*\n\n"
            "📦 *{name}*\n"
            "💰 Current Price: *{price}*\n"
            "🔔 Alert Type: *{alert}*\n"
            "🕐 {time}\n\n"
            "You'll be notified when condition is met! 🎯"
        ),
        "alert_desc_any": "Any price change",
        "alert_desc_percent": "When drops {val}% or more",
        "alert_desc_target": "When reaches {val} or less",
        "error_url": "❌ Invalid URL! Must start with https://",
        "error_price": "❌ Couldn't fetch the price!\nCheck the Selector.",
        "error_number": "❌ Please enter a valid number!\nTry again:",
        "no_products": "📋 No products tracked yet.",
        "products_title": "📋 *My Products:*\n\n",
        "no_change": "✅ No price changes\nLast check: {time}",
        "price_down": "📉 *Price Dropped!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n💰 Discount: *{pct}%* 🎉\n\n🔗 {url}",
        "price_up": "📈 *Price Increased!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n\n🔗 {url}",
        "price_target": "🎯 *Target Price Reached!*\n\n📦 *{name}*\n✅ Price now: *{new}*\n\n🔗 {url}",
        "deleted": "✅ Deleted successfully",
        "select_delete": "🗑️ Choose what to delete:",
        "cancel": "❌ Cancelled",
        "checking_all": "⏳ Checking all prices...",
        "lang_changed": "✅ Language changed 🇬🇧",
        "stats_text": (
            "📊 *Your Stats:*\n\n"
            "📦 Tracked Products: *{products}*\n"
            "🌐 Watched Sites: *{sites}*\n"
            "🕐 Last Check: *{last_check}*"
        ),
        "help_text": (
            "❓ *Usage Guide:*\n\n"
            "━━━━━━━━━━━━━━\n"
            "➕ *Add Product:*\n"
            "Send URL ← Selector ← Name ← Alert type\n\n"
            "━━━━━━━━━━━━━━\n"
            "🌐 *Watch Site:*\n"
            "Send deals page URL ← Price Selector ← Name\n"
            "Bot notifies you of new deals on the page\n\n"
            "━━━━━━━━━━━━━━\n"
            "⏰ Auto-checks every hour"
        ),
        "send_site_url": (
            "🌐 Send the deals/sale page URL:\n\n"
            "Example:\n"
            "`https://dkhoonemirates.com/collections/sale`"
        ),
        "send_site_selector": (
            "🎯 Send the CSS Selector for prices on this page:\n\n"
            "Example: `span.price`"
        ),
        "send_site_name": "✏️ Enter a name for this site:",
        "site_added": (
            "✅ *Site Added!*\n\n"
            "🌐 *{name}*\n"
            "📦 Current products found: *{count}*\n\n"
            "You'll be notified of new deals! 🔔"
        ),
        "site_new_deals": (
            "🔥 *New deals on {name}!*\n\n"
            "{deals}\n"
            "🔗 {url}"
        ),
        "no_sites": "🌐 No sites being watched yet.",
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
    return load_data().get(str(chat_id), {}).get("lang", None)

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
    return user.first_name or user.username or "صديقي"

def price_to_float(s):
    try:
        return float(re.sub(r"[^\d.]", "", str(s).replace(",", ".")))
    except:
        return None

# ─── الكيبورد ───
def main_keyboard(chat_id):
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]
    return ReplyKeyboardMarkup([
        [KeyboardButton(txt["add"]), KeyboardButton(txt["watch_site"])],
        [KeyboardButton(txt["list"]), KeyboardButton(txt["check"])],
        [KeyboardButton(txt["stats"]), KeyboardButton(txt["help"])],
        [KeyboardButton(txt["delete"]), KeyboardButton(txt["language"])],
    ], resize_keyboard=True)

# ─── جلب الأسعار ───
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
        logger.warning(f"fetch_price: {e}")
    return None

async def fetch_all_prices(url, selector):
    """جلب كل الأسعار من صفحة كاملة"""
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        elements = soup.select(selector)
        prices = []
        for el in elements[:20]:
            text = el.get_text().strip()
            match = re.search(r"[\d,،.]+", text.replace("\xa0", " "))
            if match:
                prices.append(match.group(0))
        return prices
    except Exception as e:
        logger.warning(f"fetch_all_prices: {e}")
    return []

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
        await update.message.reply_text(
            f"👋 أهلاً *{name}*! / Welcome *{name}*!\nاختار لغتك / Choose language:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🇪🇬 عربي", callback_data="lang_ar"),
                InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
            ]])
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

# ════════════════════════════════
# ─── إضافة منتج ───
# ════════════════════════════════
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

async def add_name_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx.user_data["name"] = update.message.text.strip()
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]
    await update.message.reply_text(
        t(chat_id, "choose_alert"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(txt["alert_any"], callback_data="alert_any"),
            InlineKeyboardButton(txt["alert_percent"], callback_data="alert_percent"),
            InlineKeyboardButton(txt["alert_target"], callback_data="alert_target"),
        ]])
    )
    return WAITING_ALERT_TYPE

async def alert_type_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    alert_type = query.data.replace("alert_", "")
    ctx.user_data["alert_type"] = alert_type
    await query.answer()

    if alert_type == "any":
        await query.edit_message_text("✅ " + t(chat_id, "alert_desc_any"))
        await _finish_add(ctx, chat_id)
        return ConversationHandler.END
    elif alert_type == "percent":
        await query.edit_message_text(t(chat_id, "send_percent"), parse_mode="Markdown")
        return WAITING_ALERT_VALUE
    else:
        await query.edit_message_text(t(chat_id, "send_target"), parse_mode="Markdown")
        return WAITING_ALERT_VALUE

async def alert_value_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    val = update.message.text.strip()
    try:
        float(val)
    except:
        await update.message.reply_text(t(chat_id, "error_number"))
        return WAITING_ALERT_VALUE
    ctx.user_data["alert_value"] = val
    await _finish_add(ctx, chat_id, update)
    return ConversationHandler.END

async def _finish_add(ctx, chat_id, update=None):
    cid = str(chat_id)
    url = ctx.user_data["url"]
    selector = ctx.user_data["selector"]
    name = ctx.user_data["name"]
    alert_type = ctx.user_data.get("alert_type", "any")
    alert_value = ctx.user_data.get("alert_value", None)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    msg = await ctx.bot.send_message(chat_id=chat_id, text=t(int(chat_id), "checking"))
    price = await fetch_price(url, selector)
    await msg.delete()

    if not price:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=t(int(chat_id), "error_price"),
            parse_mode="Markdown",
            reply_markup=main_keyboard(int(chat_id))
        )
        return

    lang = get_lang(chat_id) or "ar"
    if alert_type == "any":
        alert_desc = T[lang]["alert_desc_any"]
    elif alert_type == "percent":
        alert_desc = T[lang]["alert_desc_percent"].format(val=alert_value)
    else:
        alert_desc = T[lang]["alert_desc_target"].format(val=alert_value)

    data = load_data()
    if cid not in data:
        data[cid] = {}
    if "products" not in data[cid]:
        data[cid]["products"] = {}

    pid = str(len(data[cid]["products"]) + 1)
    data[cid]["products"][pid] = {
        "url": url, "selector": selector, "price": price,
        "name": name, "added": now, "last_check": now,
        "alert_type": alert_type, "alert_value": alert_value
    }
    save_data(data)

    await ctx.bot.send_message(
        chat_id=chat_id,
        text=t(int(chat_id), "added", name=name, price=price, alert=alert_desc, time=now),
        parse_mode="Markdown",
        reply_markup=main_keyboard(int(chat_id))
    )

async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "cancel"), reply_markup=main_keyboard(chat_id))
    return ConversationHandler.END

# ════════════════════════════════
# ─── مراقبة موقع كامل ───
# ════════════════════════════════
async def watch_site_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "send_site_url"), parse_mode="Markdown")
    return WAITING_SITE_URL

async def watch_site_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text(t(chat_id, "error_url"))
        return WAITING_SITE_URL
    ctx.user_data["site_url"] = url
    await update.message.reply_text(t(chat_id, "send_site_selector"), parse_mode="Markdown")
    return WAITING_SITE_SELECTOR

async def watch_site_selector(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ctx.user_data["site_selector"] = update.message.text.strip()
    await update.message.reply_text(t(chat_id, "send_site_name"))
    return WAITING_SITE_NAME

async def watch_site_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    site_name = update.message.text.strip()
    url = ctx.user_data["site_url"]
    selector = ctx.user_data["site_selector"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    await update.message.reply_text(t(int(chat_id), "checking"))
    prices = await fetch_all_prices(url, selector)

    data = load_data()
    if chat_id not in data:
        data[chat_id] = {}
    if "sites" not in data[chat_id]:
        data[chat_id]["sites"] = {}

    sid = str(len(data[chat_id]["sites"]) + 1)
    data[chat_id]["sites"][sid] = {
        "url": url, "selector": selector,
        "name": site_name, "added": now,
        "last_prices": prices, "last_check": now
    }
    save_data(data)

    await update.message.reply_text(
        t(int(chat_id), "site_added", name=site_name, count=len(prices)),
        parse_mode="Markdown",
        reply_markup=main_keyboard(int(chat_id))
    )
    return ConversationHandler.END

async def watch_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "cancel"), reply_markup=main_keyboard(chat_id))
    return ConversationHandler.END

# ════════════════════════════════
# ─── عرض المنتجات ───
# ════════════════════════════════
async def list_products(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})
    sites = data.get(chat_id, {}).get("sites", {})

    if not products and not sites:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return

    text = ""
    lang = get_lang(int(chat_id)) or "ar"

    if products:
        text += T[lang]["products_title"]
        for pid, p in products.items():
            alert_icon = "🔔" if p.get("alert_type") == "any" else "📉" if p.get("alert_type") == "percent" else "🎯"
            text += f"*{pid}.* 📦 {p['name']}\n   💰 *{p['price']}* {alert_icon}\n   🔗 {p['url'][:40]}...\n\n"

    if sites:
        text += "🌐 *المواقع تحت المراقبة:*\n\n"
        for sid, s in sites.items():
            text += f"*{sid}.* 🌐 {s['name']}\n   🔗 {s['url'][:40]}...\n\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(int(chat_id)))

# ════════════════════════════════
# ─── فحص الأسعار ───
# ════════════════════════════════
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
        if not new_price or new_price == p["price"]:
            continue

        old_f = price_to_float(p["price"])
        new_f = price_to_float(new_price)
        alert_type = p.get("alert_type", "any")
        alert_value = p.get("alert_value")
        should_notify = False
        msg_key = "price_changed"

        if alert_type == "any":
            should_notify = True
            msg_key = "price_down" if (old_f and new_f and new_f < old_f) else "price_up"
        elif alert_type == "percent" and old_f and new_f:
            pct = ((old_f - new_f) / old_f) * 100
            if new_f < old_f and pct >= float(alert_value or 0):
                should_notify = True
                msg_key = "price_down"
        elif alert_type == "target" and new_f:
            if new_f <= float(alert_value or 0):
                should_notify = True
                msg_key = "price_target"

        if should_notify:
            data[chat_id]["products"][pid]["price"] = new_price
            changed += 1
            old_f2 = price_to_float(p["price"])
            new_f2 = price_to_float(new_price)
            pct_val = round(((old_f2 - new_f2) / old_f2) * 100) if old_f2 and new_f2 else 0
            await update.message.reply_text(
                t(int(chat_id), msg_key, name=p["name"], old=p["price"], new=new_price, pct=pct_val, url=p["url"]),
                parse_mode="Markdown"
            )

    data[chat_id]["last_check"] = now
    save_data(data)

    if changed == 0:
        await update.message.reply_text(
            t(int(chat_id), "no_change", time=now),
            reply_markup=main_keyboard(int(chat_id))
        )

# ════════════════════════════════
# ─── حذف ───
# ════════════════════════════════
async def delete_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})
    sites = data.get(chat_id, {}).get("sites", {})

    if not products and not sites:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return

    keyboard = []
    for pid, p in products.items():
        keyboard.append([InlineKeyboardButton(f"📦 {p['name']} — {p['price']}", callback_data=f"del_p_{pid}")])
    for sid, s in sites.items():
        keyboard.append([InlineKeyboardButton(f"🌐 {s['name']}", callback_data=f"del_s_{sid}")])

    await update.message.reply_text(
        t(int(chat_id), "select_delete"),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    data = load_data()

    if query.data.startswith("del_p_"):
        pid = query.data.replace("del_p_", "")
        if pid in data.get(chat_id, {}).get("products", {}):
            del data[chat_id]["products"][pid]
    elif query.data.startswith("del_s_"):
        sid = query.data.replace("del_s_", "")
        if sid in data.get(chat_id, {}).get("sites", {}):
            del data[chat_id]["sites"][sid]

    save_data(data)
    await query.edit_message_text(t(int(chat_id), "deleted"), parse_mode="Markdown")
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
    products = len(user_data.get("products", {}))
    sites = len(user_data.get("sites", {}))
    last_check = user_data.get("last_check", "—")
    await update.message.reply_text(
        t(int(chat_id), "stats_text", products=products, sites=sites, last_check=last_check),
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
    await update.message.reply_text(
        "اختار اللغة / Choose language:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇪🇬 عربي", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
        ]])
    )

# ─── معالج الأزرار ───
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]

    if text == txt["list"]:       await list_products(update, ctx)
    elif text == txt["check"]:    await check_prices(update, ctx)
    elif text == txt["delete"]:   await delete_menu(update, ctx)
    elif text == txt["language"]: await change_language(update, ctx)
    elif text == txt["stats"]:    await stats(update, ctx)
    elif text == txt["help"]:     await help_cmd(update, ctx)

# ─── الفحص التلقائي ───
async def auto_check(ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for chat_id, user_data in data.items():
        # فحص المنتجات
        for pid, p in user_data.get("products", {}).items():
            new_price = await fetch_price(p["url"], p["selector"])
            if not new_price or new_price == p["price"]:
                continue

            old_f = price_to_float(p["price"])
            new_f = price_to_float(new_price)
            alert_type = p.get("alert_type", "any")
            alert_value = p.get("alert_value")
            should_notify = False
            msg_key = "price_down"

            if alert_type == "any":
                should_notify = True
                msg_key = "price_down" if (old_f and new_f and new_f < old_f) else "price_up"
            elif alert_type == "percent" and old_f and new_f and new_f < old_f:
                pct = ((old_f - new_f) / old_f) * 100
                if pct >= float(alert_value or 0):
                    should_notify = True
                    msg_key = "price_down"
            elif alert_type == "target" and new_f:
                if new_f <= float(alert_value or 0):
                    should_notify = True
                    msg_key = "price_target"

            if should_notify:
                data[chat_id]["products"][pid]["price"] = new_price
                pct_val = round(((old_f - new_f) / old_f) * 100) if old_f and new_f else 0
                try:
                    await ctx.bot.send_message(
                        chat_id=int(chat_id),
                        text=t(int(chat_id), msg_key, name=p["name"], old=p["price"], new=new_price, pct=pct_val, url=p["url"]),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"notify error: {e}")

        # فحص المواقع
        for sid, s in user_data.get("sites", {}).items():
            new_prices = await fetch_all_prices(s["url"], s["selector"])
            old_prices = set(s.get("last_prices", []))
            new_set = set(new_prices)
            new_deals = new_set - old_prices

            if new_deals:
                data[chat_id]["sites"][sid]["last_prices"] = new_prices
                deals_text = "\n".join([f"💰 {p}" for p in list(new_deals)[:10]])
                try:
                    await ctx.bot.send_message(
                        chat_id=int(chat_id),
                        text=t(int(chat_id), "site_new_deals", name=s["name"], deals=deals_text, url=s["url"]),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"site notify error: {e}")

        data[chat_id]["last_check"] = now

    save_data(data)

# ─── تشغيل البوت ───
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation: إضافة منتج
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(r"^(➕ إضافة منتج|➕ Add Product)$"), add_start),
        ],
        states={
            WAITING_URL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
            WAITING_SELECTOR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_selector)],
            WAITING_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name_step)],
            WAITING_ALERT_TYPE: [CallbackQueryHandler(alert_type_callback, pattern=r"^alert_")],
            WAITING_ALERT_VALUE:[MessageHandler(filters.TEXT & ~filters.COMMAND, alert_value_step)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    # Conversation: مراقبة موقع
    site_conv = ConversationHandler(
        entry_points=[
            CommandHandler("watch", watch_site_start),
            MessageHandler(filters.Regex(r"^(🌐 مراقبة موقع|🌐 Watch Site)$"), watch_site_start),
        ],
        states={
            WAITING_SITE_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_site_url)],
            WAITING_SITE_SELECTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_site_selector)],
            WAITING_SITE_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_site_name)],
        },
        fallbacks=[CommandHandler("cancel", watch_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del_"))
    app.add_handler(add_conv)
    app.add_handler(site_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    app.job_queue.run_repeating(auto_check, interval=CHECK_INTERVAL, first=60)

    logger.info("✅ البوت شغال!")
    app.run_polling()

if __name__ == "__main__":
    main()
