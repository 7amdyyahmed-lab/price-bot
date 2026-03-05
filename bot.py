#!/usr/bin/env python3
import json, os, re, logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
    ConversationHandler, PreCheckoutQueryHandler
)

BOT_TOKEN   = os.environ.get("BOT_TOKEN")
ADMIN_ID    = int(os.environ.get("ADMIN_ID", "0"))   # ← هتحط ID بتاعك
DATA_FILE   = "prices.json"
CHECK_INTERVAL = 3600

# أسعار الاشتراك بالـ Stars
PRICE_MONTHLY = 100   # 100 Star = شهري
PRICE_YEARLY  = 900   # 900 Star = سنوي

# حدود المجاني
FREE_LIMIT = 3

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

(WAITING_URL, WAITING_SELECTOR, WAITING_NAME,
 WAITING_ALERT_TYPE, WAITING_ALERT_VALUE) = range(5)
WAITING_SITE_URL, WAITING_SITE_SELECTOR, WAITING_SITE_NAME = range(5, 8)
WAITING_MANUAL_ID, WAITING_MANUAL_MONTHS = range(8, 10)

# ══════════════════════════════════════
# النصوص
# ══════════════════════════════════════
T = {
    "ar": {
        "choose_lang": "👋 أهلاً *{name}*!\nاختار لغتك:",
        "welcome": (
            "🎉 *أهلاً {name}!*\n\n"
            "أنا بوت تتبع الأسعار الذكي 🛒\n\n"
            "━━━━━━━━━━━━━━\n"
            "🆓 *النسخة المجانية:*\n"
            "• 3 منتجات فقط\n"
            "• تنبيه أي تغيير\n\n"
            "💎 *النسخة البريميوم:*\n"
            "• منتجات غير محدودة\n"
            "• مراقبة موقع كامل\n"
            "• تنبيه بنسبة خصم أو سعر معين\n\n"
            "━━━━━━━━━━━━━━\n"
            "اضغط أي زرار للبدء! 👇"
        ),
        "main_menu": "🏠 *القائمة الرئيسية*",
        "add": "➕ إضافة منتج",
        "watch_site": "🌐 مراقبة موقع 💎",
        "list": "📋 منتجاتي",
        "check": "🔍 فحص الأسعار",
        "delete": "🗑️ حذف",
        "subscribe": "💎 اشتراك بريميوم",
        "stats": "📊 إحصائياتي",
        "help": "❓ مساعدة",
        "language": "🌐 اللغة",
        # اشتراك
        "sub_menu": (
            "💎 *الاشتراك البريميوم*\n\n"
            "🆓 أنت دلوقتي على النسخة المجانية\n\n"
            "━━━━━━━━━━━━━━\n"
            "✨ *مميزات البريميوم:*\n"
            "• منتجات غير محدودة\n"
            "• مراقبة صفحة عروض كاملة\n"
            "• تنبيه بنسبة خصم معينة\n"
            "• تنبيه لما يوصل سعر معين\n\n"
            "━━━━━━━━━━━━━━\n"
            "💳 *اختار طريقة الدفع:*"
        ),
        "sub_active": (
            "💎 *اشتراكك نشط!*\n\n"
            "📅 ينتهي في: *{date}*\n"
            "⏳ متبقي: *{days}* يوم\n\n"
            "شكراً لدعمك! 🙏"
        ),
        "stars_monthly": "⭐ شهري — 100 Stars",
        "stars_yearly": "⭐ سنوي — 900 Stars",
        "vodafone_pay": "💸 Vodafone Cash / تحويل",
        "stars_invoice_monthly": "اشتراك شهري — بوت تتبع الأسعار",
        "stars_invoice_yearly": "اشتراك سنوي — بوت تتبع الأسعار",
        "sub_success": (
            "🎉 *تم الاشتراك بنجاح!*\n\n"
            "💎 أنت الآن عضو بريميوم\n"
            "📅 ينتهي في: *{date}*\n\n"
            "استمتع بكل المميزات! 🚀"
        ),
        "vodafone_instructions": (
            "💸 *الدفع عن طريق Vodafone Cash:*\n\n"
            "1️⃣ حول المبلغ لـ: `01XXXXXXXXX`\n"
            "2️⃣ ابعت صورة الإيصال هنا\n"
            "3️⃣ انتظر تأكيد الأدمن\n\n"
            "💰 الأسعار:\n"
            "• شهري: *XX جنيه*\n"
            "• سنوي: *XX جنيه*\n\n"
            "⏰ التفعيل خلال ساعة"
        ),
        "free_limit": (
            "⚠️ *وصلت للحد المجاني!*\n\n"
            "النسخة المجانية بتسمح بـ 3 منتجات فقط.\n\n"
            "💎 اشترك في البريميوم للإضافة بدون حدود!"
        ),
        "premium_only": (
            "💎 *هذه الميزة للمشتركين فقط!*\n\n"
            "اشترك في البريميوم للاستفادة من:\n"
            "• مراقبة موقع كامل\n"
            "• تنبيهات متقدمة\n"
            "• منتجات غير محدودة"
        ),
        # إضافة منتج
        "send_url": "🔗 أرسل رابط المنتج:",
        "send_selector": (
            "🎯 أرسل CSS Selector للسعر:\n\n"
            "مثال: `span.price`\n\n"
            "إزاي تجيبه؟\n"
            "1️⃣ Chrome ← كليك يمين على السعر\n"
            "2️⃣ Inspect\n"
            "3️⃣ كليك يمين على العنصر\n"
            "4️⃣ Copy ← Copy selector"
        ),
        "send_name": "✏️ اكتب اسم للمنتج:",
        "choose_alert": "🔔 *اختار نوع التنبيه:*",
        "alert_any": "🔔 أي تغيير",
        "alert_percent": "📉 نسبة خصم 💎",
        "alert_target": "🎯 سعر معين 💎",
        "send_percent": "📉 اكتب نسبة الخصم:\n\nمثال: `30` = لما ينزل 30% أو أكتر",
        "send_target": "🎯 اكتب السعر المطلوب:\n\nمثال: `100` = لما يوصل 100 أو أقل",
        "checking": "⏳ جاري فحص السعر...",
        "added": "✅ *تمت الإضافة!*\n\n📦 *{name}*\n💰 السعر: *{price}*\n🔔 التنبيه: *{alert}*",
        "alert_desc_any": "أي تغيير",
        "alert_desc_percent": "نزول {val}% أو أكتر",
        "alert_desc_target": "وصول {val} أو أقل",
        "error_url": "❌ الرابط غلط! لازم يبدأ بـ https://",
        "error_price": "❌ مقدرتش أجيب السعر!\nتأكد من الـ Selector.",
        "error_number": "❌ لازم تكتب رقم!\nحاول تاني:",
        "no_products": "📋 مفيش منتجات دلوقتي.",
        "products_title": "📋 *منتجاتي:*\n\n",
        "no_change": "✅ مفيش تغييرات\nآخر فحص: {time}",
        "price_down": "📉 *انخفض السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n💰 خصم: *{pct}%* 🎉\n\n🔗 {url}",
        "price_up": "📈 *ارتفع السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n\n🔗 {url}",
        "price_target": "🎯 *وصل للسعر!*\n\n📦 *{name}*\n✅ السعر: *{new}*\n\n🔗 {url}",
        "deleted": "✅ تم الحذف",
        "select_delete": "🗑️ اختار اللي عايز تحذفه:",
        "cancel": "❌ تم الإلغاء",
        "checking_all": "⏳ جاري فحص الأسعار...",
        "lang_changed": "✅ تم تغيير اللغة 🇪🇬",
        "stats_text": "📊 *إحصائياتك:*\n\n📦 منتجات: *{products}*\n🌐 مواقع: *{sites}*\n💎 الاشتراك: *{sub}*\n🕐 آخر فحص: *{last_check}*",
        "sub_status_free": "مجاني",
        "sub_status_premium": "بريميوم حتى {date}",
        "help_text": (
            "❓ *دليل الاستخدام:*\n\n"
            "➕ *إضافة منتج:*\n"
            "رابط ← Selector ← اسم ← نوع التنبيه\n\n"
            "🌐 *مراقبة موقع (بريميوم):*\n"
            "رابط صفحة العروض ← Selector ← اسم\n\n"
            "💎 *البريميوم:*\n"
            "اضغط زرار الاشتراك واختار طريقة الدفع\n\n"
            "⏰ الفحص التلقائي كل ساعة"
        ),
        "send_site_url": "🌐 أرسل رابط صفحة العروض:\n\nمثال: `https://site.com/sale`",
        "send_site_selector": "🎯 أرسل Selector للأسعار في الصفحة:",
        "send_site_name": "✏️ اكتب اسم للموقع:",
        "site_added": "✅ *تمت إضافة الموقع!*\n\n🌐 *{name}*\n📦 منتجات موجودة: *{count}*",
        "site_new_deals": "🔥 *عروض جديدة على {name}!*\n\n{deals}\n\n🔗 {url}",
        # أدمن
        "admin_menu": "👑 *لوحة الأدمن:*",
        "admin_users": "👥 المستخدمين",
        "admin_add_sub": "➕ إضافة اشتراك يدوي",
        "admin_broadcast": "📢 رسالة للكل",
        "users_list": "👥 *المستخدمين:*\n\n{list}",
        "enter_user_id": "أرسل ID المستخدم:",
        "enter_months": "أرسل عدد الأشهر:",
        "sub_added_admin": "✅ تم تفعيل الاشتراك للمستخدم {uid} لمدة {months} شهر",
        "sub_activated_user": "💎 *تم تفعيل اشتراكك البريميوم!*\n\n📅 ينتهي في: *{date}*\n\nشكراً لك! 🎉",
        "not_admin": "❌ مش أدمن!",
        "vodafone_received": "✅ تم استلام طلب الدفع!\nالأدمن هيتواصل معاك خلال ساعة 🕐",
    },
    "en": {
        "choose_lang": "👋 Hello *{name}*!\nChoose language:",
        "welcome": (
            "🎉 *Welcome {name}!*\n\n"
            "Smart Price Tracker Bot 🛒\n\n"
            "━━━━━━━━━━━━━━\n"
            "🆓 *Free Plan:*\n"
            "• 3 products only\n"
            "• Any change alert\n\n"
            "💎 *Premium Plan:*\n"
            "• Unlimited products\n"
            "• Full site monitoring\n"
            "• % discount & target price alerts\n\n"
            "━━━━━━━━━━━━━━\n"
            "Tap any button to start! 👇"
        ),
        "main_menu": "🏠 *Main Menu*",
        "add": "➕ Add Product",
        "watch_site": "🌐 Watch Site 💎",
        "list": "📋 My Products",
        "check": "🔍 Check Prices",
        "delete": "🗑️ Delete",
        "subscribe": "💎 Premium",
        "stats": "📊 My Stats",
        "help": "❓ Help",
        "language": "🌐 Language",
        "sub_menu": (
            "💎 *Premium Subscription*\n\n"
            "🆓 You're on the Free plan\n\n"
            "━━━━━━━━━━━━━━\n"
            "✨ *Premium Features:*\n"
            "• Unlimited products\n"
            "• Full site monitoring\n"
            "• % discount alerts\n"
            "• Target price alerts\n\n"
            "━━━━━━━━━━━━━━\n"
            "💳 *Choose payment method:*"
        ),
        "sub_active": "💎 *Subscription Active!*\n\n📅 Expires: *{date}*\n⏳ Days left: *{days}*\n\nThank you! 🙏",
        "stars_monthly": "⭐ Monthly — 100 Stars",
        "stars_yearly": "⭐ Yearly — 900 Stars",
        "vodafone_pay": "💸 Bank Transfer",
        "stars_invoice_monthly": "Monthly Subscription — Price Tracker Bot",
        "stars_invoice_yearly": "Yearly Subscription — Price Tracker Bot",
        "sub_success": "🎉 *Subscribed Successfully!*\n\n💎 You're now Premium\n📅 Expires: *{date}*\n\nEnjoy all features! 🚀",
        "vodafone_instructions": "💸 *Bank Transfer Payment:*\n\n1️⃣ Transfer amount to: `XXXXXXXXX`\n2️⃣ Send receipt here\n3️⃣ Wait for admin confirmation\n\n💰 Prices:\n• Monthly: *XX EGP*\n• Yearly: *XX EGP*\n\n⏰ Activation within 1 hour",
        "free_limit": "⚠️ *Free limit reached!*\n\nFree plan allows 3 products only.\n\n💎 Subscribe to Premium for unlimited products!",
        "premium_only": "💎 *Premium feature only!*\n\nSubscribe to access:\n• Full site monitoring\n• Advanced alerts\n• Unlimited products",
        "send_url": "🔗 Send the product URL:",
        "send_selector": "🎯 Send CSS Selector for price:\n\nExample: `span.price`",
        "send_name": "✏️ Enter product name:",
        "choose_alert": "🔔 *Choose alert type:*",
        "alert_any": "🔔 Any Change",
        "alert_percent": "📉 % Discount 💎",
        "alert_target": "🎯 Target Price 💎",
        "send_percent": "📉 Enter discount %:\n\nExample: `30` = alert when drops 30% or more",
        "send_target": "🎯 Enter target price:\n\nExample: `100` = alert when reaches 100 or less",
        "checking": "⏳ Checking price...",
        "added": "✅ *Added!*\n\n📦 *{name}*\n💰 Price: *{price}*\n🔔 Alert: *{alert}*",
        "alert_desc_any": "Any change",
        "alert_desc_percent": "Drop {val}% or more",
        "alert_desc_target": "Reach {val} or less",
        "error_url": "❌ Invalid URL! Must start with https://",
        "error_price": "❌ Couldn't fetch price!\nCheck the Selector.",
        "error_number": "❌ Enter a valid number!\nTry again:",
        "no_products": "📋 No products tracked yet.",
        "products_title": "📋 *My Products:*\n\n",
        "no_change": "✅ No changes\nLast check: {time}",
        "price_down": "📉 *Price Dropped!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n💰 Discount: *{pct}%* 🎉\n\n🔗 {url}",
        "price_up": "📈 *Price Increased!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n\n🔗 {url}",
        "price_target": "🎯 *Target Reached!*\n\n📦 *{name}*\n✅ Price: *{new}*\n\n🔗 {url}",
        "deleted": "✅ Deleted",
        "select_delete": "🗑️ Choose to delete:",
        "cancel": "❌ Cancelled",
        "checking_all": "⏳ Checking all prices...",
        "lang_changed": "✅ Language changed 🇬🇧",
        "stats_text": "📊 *Your Stats:*\n\n📦 Products: *{products}*\n🌐 Sites: *{sites}*\n💎 Plan: *{sub}*\n🕐 Last Check: *{last_check}*",
        "sub_status_free": "Free",
        "sub_status_premium": "Premium until {date}",
        "help_text": "❓ *Usage Guide:*\n\n➕ *Add Product:*\nURL ← Selector ← Name ← Alert type\n\n🌐 *Watch Site (Premium):*\nDeals page URL ← Selector ← Name\n\n💎 *Premium:*\nTap Subscribe button\n\n⏰ Auto-check every hour",
        "send_site_url": "🌐 Send deals page URL:\n\nExample: `https://site.com/sale`",
        "send_site_selector": "🎯 Send Selector for prices on this page:",
        "send_site_name": "✏️ Enter site name:",
        "site_added": "✅ *Site Added!*\n\n🌐 *{name}*\n📦 Products found: *{count}*",
        "site_new_deals": "🔥 *New deals on {name}!*\n\n{deals}\n\n🔗 {url}",
        "admin_menu": "👑 *Admin Panel:*",
        "admin_users": "👥 Users",
        "admin_add_sub": "➕ Add Subscription",
        "admin_broadcast": "📢 Broadcast",
        "users_list": "👥 *Users:*\n\n{list}",
        "enter_user_id": "Send user ID:",
        "enter_months": "Send number of months:",
        "sub_added_admin": "✅ Subscription activated for {uid} for {months} months",
        "sub_activated_user": "💎 *Your Premium subscription is active!*\n\n📅 Expires: *{date}*\n\nThank you! 🎉",
        "not_admin": "❌ Not admin!",
        "vodafone_received": "✅ Payment request received!\nAdmin will contact you within 1 hour 🕐",
    }
}

# ══════════════════════════════════════
# البيانات
# ══════════════════════════════════════
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

# ── اشتراك ──
def is_premium(chat_id):
    data = load_data()
    sub = data.get(str(chat_id), {}).get("subscription")
    if not sub:
        return False
    expiry = datetime.fromisoformat(sub["expiry"])
    return expiry > datetime.now()

def add_subscription(chat_id, months):
    data = load_data()
    cid = str(chat_id)
    if cid not in data:
        data[cid] = {}
    now = datetime.now()
    current = data[cid].get("subscription")
    if current:
        try:
            base = datetime.fromisoformat(current["expiry"])
            if base > now:
                expiry = base + timedelta(days=30 * months)
            else:
                expiry = now + timedelta(days=30 * months)
        except:
            expiry = now + timedelta(days=30 * months)
    else:
        expiry = now + timedelta(days=30 * months)
    data[cid]["subscription"] = {"expiry": expiry.isoformat(), "activated": now.isoformat()}
    save_data(data)
    return expiry.strftime("%Y-%m-%d")

# ══════════════════════════════════════
# الكيبورد
# ══════════════════════════════════════
def main_keyboard(chat_id):
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]
    return ReplyKeyboardMarkup([
        [KeyboardButton(txt["add"]), KeyboardButton(txt["watch_site"])],
        [KeyboardButton(txt["list"]), KeyboardButton(txt["check"])],
        [KeyboardButton(txt["subscribe"]), KeyboardButton(txt["stats"])],
        [KeyboardButton(txt["delete"]), KeyboardButton(txt["help"])],
        [KeyboardButton(txt["language"])],
    ], resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("👥 المستخدمين"), KeyboardButton("➕ اشتراك يدوي")],
        [KeyboardButton("📊 إحصائيات"), KeyboardButton("🏠 رجوع")],
    ], resize_keyboard=True)

# ══════════════════════════════════════
# جلب الأسعار
# ══════════════════════════════════════
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
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        prices = []
        for el in soup.select(selector)[:20]:
            text = el.get_text().strip()
            match = re.search(r"[\d,،.]+", text.replace("\xa0", " "))
            if match:
                prices.append(match.group(0))
        return prices
    except:
        return []

# ══════════════════════════════════════
# Start & اللغة
# ══════════════════════════════════════
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = get_name(update.effective_user)
    lang = get_lang(chat_id)
    # سجل المستخدم
    data = load_data()
    cid = str(chat_id)
    if cid not in data:
        data[cid] = {"joined": datetime.now().isoformat()}
        save_data(data)
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

# ══════════════════════════════════════
# الاشتراك
# ══════════════════════════════════════
async def subscribe_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]

    if is_premium(chat_id):
        data = load_data()
        sub = data[str(chat_id)]["subscription"]
        expiry = datetime.fromisoformat(sub["expiry"])
        days = (expiry - datetime.now()).days
        await update.message.reply_text(
            t(chat_id, "sub_active", date=expiry.strftime("%Y-%m-%d"), days=days),
            parse_mode="Markdown",
            reply_markup=main_keyboard(chat_id)
        )
        return

    await update.message.reply_text(
        t(chat_id, "sub_menu"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(txt["stars_monthly"], callback_data="pay_stars_1")],
            [InlineKeyboardButton(txt["stars_yearly"],  callback_data="pay_stars_12")],
            [InlineKeyboardButton(txt["vodafone_pay"],  callback_data="pay_vodafone")],
        ])
    )

async def payment_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()

    if query.data == "pay_stars_1":
        await ctx.bot.send_invoice(
            chat_id=chat_id,
            title="💎 اشتراك شهري" if (get_lang(chat_id) or "ar") == "ar" else "💎 Monthly Premium",
            description=t(chat_id, "stars_invoice_monthly"),
            payload="premium_1month",
            currency="XTR",
            prices=[LabeledPrice("Premium Monthly", PRICE_MONTHLY)],
        )
    elif query.data == "pay_stars_12":
        await ctx.bot.send_invoice(
            chat_id=chat_id,
            title="💎 اشتراك سنوي" if (get_lang(chat_id) or "ar") == "ar" else "💎 Yearly Premium",
            description=t(chat_id, "stars_invoice_yearly"),
            payload="premium_12months",
            currency="XTR",
            prices=[LabeledPrice("Premium Yearly", PRICE_YEARLY)],
        )
    elif query.data == "pay_vodafone":
        await query.edit_message_text(
            t(chat_id, "vodafone_instructions"),
            parse_mode="Markdown"
        )
        # بعت إشعار للأدمن
        if ADMIN_ID:
            data = load_data()
            name = data.get(str(chat_id), {}).get("first_name", str(chat_id))
            await ctx.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💸 *طلب دفع جديد!*\n\nالمستخدم: {chat_id}\nالاسم: {name}",
                parse_mode="Markdown"
            )
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=t(chat_id, "vodafone_received"),
            reply_markup=main_keyboard(chat_id)
        )

async def precheckout_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    payload = update.message.successful_payment.invoice_payload
    months = 12 if "12months" in payload else 1
    expiry_date = add_subscription(chat_id, months)
    await update.message.reply_text(
        t(chat_id, "sub_success", date=expiry_date),
        parse_mode="Markdown",
        reply_markup=main_keyboard(chat_id)
    )
    if ADMIN_ID:
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⭐ *دفع ناجح!*\nالمستخدم: {chat_id}\nالمدة: {months} شهر",
            parse_mode="Markdown"
        )

# ══════════════════════════════════════
# إضافة منتج
# ══════════════════════════════════════
async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = load_data()
    products = data.get(str(chat_id), {}).get("products", {})
    if not is_premium(chat_id) and len(products) >= FREE_LIMIT:
        await update.message.reply_text(
            t(chat_id, "free_limit"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 اشترك الآن", callback_data="open_sub")
            ]])
        )
        return ConversationHandler.END
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
    premium = is_premium(chat_id)
    keyboard = [[
        InlineKeyboardButton(txt["alert_any"], callback_data="alert_any"),
    ]]
    if premium:
        keyboard.append([
            InlineKeyboardButton(txt["alert_percent"], callback_data="alert_percent"),
            InlineKeyboardButton(txt["alert_target"],  callback_data="alert_target"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 " + txt["alert_percent"] + " (بريميوم)", callback_data="alert_locked")])
        keyboard.append([InlineKeyboardButton("🔒 " + txt["alert_target"]  + " (بريميوم)", callback_data="alert_locked")])

    await update.message.reply_text(
        t(chat_id, "choose_alert"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_ALERT_TYPE

async def alert_type_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()

    if query.data == "alert_locked":
        await query.answer(t(chat_id, "premium_only"), show_alert=True)
        return WAITING_ALERT_TYPE

    ctx.user_data["alert_type"] = query.data.replace("alert_", "")
    if ctx.user_data["alert_type"] == "any":
        await query.edit_message_text("✅ " + t(chat_id, "alert_desc_any"))
        await _finish_add(ctx, chat_id)
        return ConversationHandler.END
    elif ctx.user_data["alert_type"] == "percent":
        await query.edit_message_text(t(chat_id, "send_percent"), parse_mode="Markdown")
    else:
        await query.edit_message_text(t(chat_id, "send_target"), parse_mode="Markdown")
    return WAITING_ALERT_VALUE

async def alert_value_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        float(update.message.text.strip())
    except:
        await update.message.reply_text(t(chat_id, "error_number"))
        return WAITING_ALERT_VALUE
    ctx.user_data["alert_value"] = update.message.text.strip()
    await _finish_add(ctx, chat_id, update)
    return ConversationHandler.END

async def _finish_add(ctx, chat_id, update=None):
    cid = str(chat_id)
    url = ctx.user_data["url"]
    selector = ctx.user_data["selector"]
    name = ctx.user_data["name"]
    alert_type = ctx.user_data.get("alert_type", "any")
    alert_value = ctx.user_data.get("alert_value")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lang = get_lang(chat_id) or "ar"

    msg = await ctx.bot.send_message(chat_id=chat_id, text=t(int(chat_id), "checking"))
    price = await fetch_price(url, selector)
    await msg.delete()

    if not price:
        await ctx.bot.send_message(chat_id=chat_id, text=t(int(chat_id), "error_price"), parse_mode="Markdown", reply_markup=main_keyboard(int(chat_id)))
        return

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
        text=t(int(chat_id), "added", name=name, price=price, alert=alert_desc),
        parse_mode="Markdown",
        reply_markup=main_keyboard(int(chat_id))
    )

async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "cancel"), reply_markup=main_keyboard(chat_id))
    return ConversationHandler.END

# ══════════════════════════════════════
# مراقبة موقع
# ══════════════════════════════════════
async def watch_site_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_premium(chat_id):
        await update.message.reply_text(
            t(chat_id, "premium_only"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 اشترك", callback_data="open_sub")]])
        )
        return ConversationHandler.END
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

# ══════════════════════════════════════
# عرض / فحص / حذف
# ══════════════════════════════════════
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
            icon = "🔔" if p.get("alert_type") == "any" else "📉" if p.get("alert_type") == "percent" else "🎯"
            text += f"*{pid}.* 📦 {p['name']}\n   💰 *{p['price']}* {icon}\n   🔗 {p['url'][:40]}...\n\n"
    if sites:
        text += "🌐 *المواقع:*\n\n"
        for sid, s in sites.items():
            text += f"*{sid}.* 🌐 {s['name']}\n   🔗 {s['url'][:40]}...\n\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(int(chat_id)))

async def check_prices(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not products:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return

    await update.message.reply_text(t(int(chat_id), "checking_all"))

    for pid, p in products.items():
        new_price = await fetch_price(p["url"], p["selector"])
        if not new_price or new_price == p["price"]:
            continue
        old_f, new_f = price_to_float(p["price"]), price_to_float(new_price)
        alert_type = p.get("alert_type", "any")
        alert_value = p.get("alert_value")
        should_notify = False
        msg_key = "price_down"

        if alert_type == "any":
            should_notify = True
            msg_key = "price_down" if (old_f and new_f and new_f < old_f) else "price_up"
        elif alert_type == "percent" and old_f and new_f and new_f < old_f:
            if ((old_f - new_f) / old_f * 100) >= float(alert_value or 0):
                should_notify = True
        elif alert_type == "target" and new_f and new_f <= float(alert_value or 0):
            should_notify = True
            msg_key = "price_target"

        if should_notify:
            data[chat_id]["products"][pid]["price"] = new_price
            pct = round((old_f - new_f) / old_f * 100) if old_f and new_f else 0
            await update.message.reply_text(
                t(int(chat_id), msg_key, name=p["name"], old=p["price"], new=new_price, pct=pct, url=p["url"]),
                parse_mode="Markdown"
            )

    data[chat_id]["last_check"] = now
    save_data(data)
    await update.message.reply_text(t(int(chat_id), "no_change", time=now), reply_markup=main_keyboard(int(chat_id)))

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

    await update.message.reply_text(t(int(chat_id), "select_delete"), reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    data = load_data()

    if query.data.startswith("del_p_"):
        pid = query.data.replace("del_p_", "")
        data.get(chat_id, {}).get("products", {}).pop(pid, None)
    elif query.data.startswith("del_s_"):
        sid = query.data.replace("del_s_", "")
        data.get(chat_id, {}).get("sites", {}).pop(sid, None)

    save_data(data)
    await query.edit_message_text(t(int(chat_id), "deleted"))
    await ctx.bot.send_message(chat_id=int(chat_id), text=t(int(chat_id), "main_menu"), parse_mode="Markdown", reply_markup=main_keyboard(int(chat_id)))

# ══════════════════════════════════════
# إحصائيات ومساعدة
# ══════════════════════════════════════
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    user_data = data.get(chat_id, {})
    products = len(user_data.get("products", {}))
    sites = len(user_data.get("sites", {}))
    last_check = user_data.get("last_check", "—")
    lang = get_lang(int(chat_id)) or "ar"

    if is_premium(int(chat_id)):
        sub = user_data.get("subscription", {})
        expiry = datetime.fromisoformat(sub["expiry"]).strftime("%Y-%m-%d")
        sub_text = T[lang]["sub_status_premium"].format(date=expiry)
    else:
        sub_text = T[lang]["sub_status_free"]

    await update.message.reply_text(
        t(int(chat_id), "stats_text", products=products, sites=sites, sub=sub_text, last_check=last_check),
        parse_mode="Markdown",
        reply_markup=main_keyboard(int(chat_id))
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "help_text"), parse_mode="Markdown", reply_markup=main_keyboard(chat_id))

async def change_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "اختار اللغة / Choose language:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇪🇬 عربي", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
        ]])
    )

# ══════════════════════════════════════
# لوحة الأدمن
# ══════════════════════════════════════
async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text(t(chat_id, "not_admin"))
        return
    data = load_data()
    total = len([k for k in data.keys() if k.isdigit()])
    premium_count = sum(1 for k, v in data.items() if k.isdigit() and is_premium(int(k)))
    await update.message.reply_text(
        f"👑 *لوحة الأدمن*\n\n👥 المستخدمين: *{total}*\n💎 المشتركين: *{premium_count}*",
        parse_mode="Markdown",
        reply_markup=admin_keyboard()
    )

async def admin_add_sub_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        return
    await update.message.reply_text(t(chat_id, "enter_user_id"))
    return WAITING_MANUAL_ID

async def admin_add_sub_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["manual_uid"] = int(update.message.text.strip())
        await update.message.reply_text(t(update.effective_chat.id, "enter_months"))
        return WAITING_MANUAL_MONTHS
    except:
        await update.message.reply_text("❌ ID غلط!")
        return WAITING_MANUAL_ID

async def admin_add_sub_months(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        months = int(update.message.text.strip())
        uid = ctx.user_data["manual_uid"]
        expiry_date = add_subscription(uid, months)
        await update.message.reply_text(
            t(chat_id, "sub_added_admin", uid=uid, months=months),
            reply_markup=admin_keyboard()
        )
        await ctx.bot.send_message(
            chat_id=uid,
            text=t(uid, "sub_activated_user", date=expiry_date),
            parse_mode="Markdown",
            reply_markup=main_keyboard(uid)
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
    return ConversationHandler.END

# ══════════════════════════════════════
# معالج الأزرار
# ══════════════════════════════════════
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    lang = get_lang(chat_id) or "ar"
    txt = T[lang]

    if text == txt["list"]:         await list_products(update, ctx)
    elif text == txt["check"]:      await check_prices(update, ctx)
    elif text == txt["delete"]:     await delete_menu(update, ctx)
    elif text == txt["language"]:   await change_language(update, ctx)
    elif text == txt["stats"]:      await stats(update, ctx)
    elif text == txt["help"]:       await help_cmd(update, ctx)
    elif text == txt["subscribe"]:  await subscribe_menu(update, ctx)
    elif text == "👑 أدمن":         await admin_panel(update, ctx)
    elif text == "🏠 رجوع":
        await update.message.reply_text(t(chat_id, "main_menu"), parse_mode="Markdown", reply_markup=main_keyboard(chat_id))

async def open_sub_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await subscribe_menu(query, ctx)

# ══════════════════════════════════════
# الفحص التلقائي
# ══════════════════════════════════════
async def auto_check(ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for chat_id, user_data in data.items():
        if not chat_id.isdigit():
            continue
        for pid, p in user_data.get("products", {}).items():
            new_price = await fetch_price(p["url"], p["selector"])
            if not new_price or new_price == p["price"]:
                continue
            old_f, new_f = price_to_float(p["price"]), price_to_float(new_price)
            alert_type = p.get("alert_type", "any")
            alert_value = p.get("alert_value")
            should_notify = False
            msg_key = "price_down"

            if alert_type == "any":
                should_notify = True
                msg_key = "price_down" if (old_f and new_f and new_f < old_f) else "price_up"
            elif alert_type == "percent" and old_f and new_f and new_f < old_f:
                if ((old_f - new_f) / old_f * 100) >= float(alert_value or 0):
                    should_notify = True
            elif alert_type == "target" and new_f and new_f <= float(alert_value or 0):
                should_notify = True
                msg_key = "price_target"

            if should_notify:
                data[chat_id]["products"][pid]["price"] = new_price
                pct = round((old_f - new_f) / old_f * 100) if old_f and new_f else 0
                try:
                    await ctx.bot.send_message(
                        chat_id=int(chat_id),
                        text=t(int(chat_id), msg_key, name=p["name"], old=p["price"], new=new_price, pct=pct, url=p["url"]),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"notify: {e}")

        for sid, s in user_data.get("sites", {}).items():
            new_prices = await fetch_all_prices(s["url"], s["selector"])
            new_deals = set(new_prices) - set(s.get("last_prices", []))
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
                    logger.error(f"site notify: {e}")

        data[chat_id]["last_check"] = now
    save_data(data)

# ══════════════════════════════════════
# تشغيل البوت
# ══════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(r"^(➕ إضافة منتج|➕ Add Product)$"), add_start),
        ],
        states={
            WAITING_URL:         [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
            WAITING_SELECTOR:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_selector)],
            WAITING_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name_step)],
            WAITING_ALERT_TYPE:  [CallbackQueryHandler(alert_type_callback, pattern=r"^alert_")],
            WAITING_ALERT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_value_step)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    site_conv = ConversationHandler(
        entry_points=[
            CommandHandler("watch", watch_site_start),
            MessageHandler(filters.Regex(r"^(🌐 مراقبة موقع 💎|🌐 Watch Site 💎)$"), watch_site_start),
        ],
        states={
            WAITING_SITE_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_site_url)],
            WAITING_SITE_SELECTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_site_selector)],
            WAITING_SITE_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, watch_site_name)],
        },
        fallbacks=[CommandHandler("cancel", watch_cancel)],
    )

    admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ اشتراك يدوي$"), admin_add_sub_start)],
        states={
            WAITING_MANUAL_ID:     [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_sub_id)],
            WAITING_MANUAL_MONTHS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_sub_months)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(CallbackQueryHandler(lang_callback,     pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(payment_callback,  pattern=r"^pay_"))
    app.add_handler(CallbackQueryHandler(delete_callback,   pattern=r"^del_"))
    app.add_handler(CallbackQueryHandler(open_sub_callback, pattern=r"^open_sub$"))
    app.add_handler(add_conv)
    app.add_handler(site_conv)
    app.add_handler(admin_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    app.job_queue.run_repeating(auto_check, interval=CHECK_INTERVAL, first=60)

    logger.info("✅ البوت شغال!")
    app.run_polling()

if __name__ == "__main__":
    main()
