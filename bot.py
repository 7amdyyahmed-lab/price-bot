#!/usr/bin/env python3
import json, os, re, logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

BOT_TOKEN  = os.environ.get("BOT_TOKEN")
ADMIN_ID   = int(os.environ.get("ADMIN_ID", "0"))
DATA_FILE  = "prices.json"

CHECK_FREE    = 3600       # المجاني: كل ساعة
CHECK_PREMIUM = 1800       # البريميوم: كل نص ساعة
DAILY_REPORT  = 86400      # تقرير يومي

FREE_LIMIT = 3

PLANS = {
    "1":  {"months": 1,  "price": 50,  "label": "شهر واحد — 50 جنيه"},
    "2":  {"months": 2,  "price": 100, "label": "شهرين — 100 جنيه"},
    "3":  {"months": 3,  "price": 120, "label": "3 شهور — 120 جنيه"},
    "12": {"months": 12, "price": 500, "label": "سنوي — 500 جنيه"},
}

PAYMENT_METHODS = {
    "instapay":  "💳 InstaPay",
    "vodafone":  "📱 Vodafone Cash",
    "orange":    "🟠 Orange Cash",
    "etisalat":  "🔵 Etisalat Cash",
}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

(WAITING_URL, WAITING_SELECTOR, WAITING_NAME,
 WAITING_ALERT_TYPE, WAITING_ALERT_VALUE) = range(5)
WAITING_SITE_URL, WAITING_SITE_SELECTOR, WAITING_SITE_NAME = range(5, 8)
WAITING_CONTACT_MSG = 8
WAITING_ADMIN_SUB_ID, WAITING_ADMIN_SUB_MONTHS = range(9, 11)
WAITING_BROADCAST = 11

# ══════════════════════════════════════════
# البيانات
# ══════════════════════════════════════════
def load_data():
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_lang(chat_id):
    return load_data().get(str(chat_id), {}).get("lang", "ar")

def set_lang(chat_id, lang):
    data = load_data()
    cid = str(chat_id)
    if cid not in data:
        data[cid] = {}
    data[cid]["lang"] = lang
    save_data(data)

def t(chat_id, key, **kwargs):
    lang = get_lang(chat_id)
    text = T[lang].get(key, key)
    return text.format(**kwargs) if kwargs else text

def get_name(user):
    return user.first_name or user.username or "مستخدم"

def price_to_float(s):
    try:
        return float(re.sub(r"[^\d.]", "", str(s).replace(",", ".")))
    except:
        return None

def is_premium(chat_id):
    data = load_data()
    sub = data.get(str(chat_id), {}).get("subscription")
    if not sub:
        return False
    return datetime.fromisoformat(sub["expiry"]) > datetime.now()

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
            expiry = (base if base > now else now) + timedelta(days=30 * months)
        except:
            expiry = now + timedelta(days=30 * months)
    else:
        expiry = now + timedelta(days=30 * months)
    data[cid]["subscription"] = {"expiry": expiry.isoformat(), "activated": now.isoformat()}
    save_data(data)
    return expiry.strftime("%Y-%m-%d")


def get_subscription_expiry(chat_id):
    data = load_data()
    sub = data.get(str(chat_id), {}).get("subscription")
    if not sub:
        return None
    try:
        return datetime.fromisoformat(sub["expiry"])
    except:
        return None

def remaining_seconds(chat_id):
    expiry = get_subscription_expiry(chat_id)
    if not expiry:
        return 0
    sec = int((expiry - datetime.now()).total_seconds())
    return max(sec, 0)

def format_remaining_seconds(sec: int):
    # Returns: "Xd Xh Xm Xs (YYYY seconds)"
    days = sec // 86400
    sec2 = sec % 86400
    hours = sec2 // 3600
    sec2 %= 3600
    minutes = sec2 // 60
    seconds = sec2 % 60
    return f"{days}d {hours}h {minutes}m {seconds}s", sec

def get_user_info(chat_id):
    data = load_data()
    return data.get(str(chat_id), {})

# ══════════════════════════════════════════
# النصوص
# ══════════════════════════════════════════
T = {
    "ar": {
        "welcome": (
            "🎉 *أهلاً {name}!*\n\n"
            "أنا بوت تتبع الأسعار الذكي 🛒\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🆓 *النسخة المجانية:*\n"
            "   • 3 منتجات فقط\n"
            "   • فحص كل ساعة\n"
            "   • تنبيه أي تغيير\n\n"
            "💎 *النسخة البريميوم:*\n"
            "   • منتجات غير محدودة\n"
            "   • فحص كل 30 دقيقة ⚡\n"
            "   • مراقبة موقع كامل\n"
            "   • تنبيه بنسبة أو سعر معين\n"
            "   • تقرير يومي بالأسعار 📊\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "اضغط أي زرار للبدء 👇"
        ),
        "main_menu": "🏠 القائمة الرئيسية",
        "add": "➕ إضافة منتج",
        "watch_site": "🌐 مراقبة موقع 💎",
        "list": "📋 منتجاتي",
        "check": "🔍 فحص الأسعار",
        "subscribe": "💎 الاشتراك",
        "dashboard": "📌 داشبورد",
        "delete": "🗑️ حذف",
        "stats": "📊 إحصائياتي",
        "help": "❓ مساعدة",
        "contact": "💬 تواصل مع الأدمن",
        "language": "🌐 اللغة",
        # اشتراك
        "sub_menu": (
            "💎 *الاشتراك البريميوم*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✨ *مميزات البريميوم:*\n"
            "   ✅ منتجات غير محدودة\n"
            "   ✅ فحص كل 30 دقيقة ⚡\n"
            "   ✅ مراقبة موقع كامل\n"
            "   ✅ تنبيه بنسبة خصم\n"
            "   ✅ تنبيه بسعر معين\n"
            "   ✅ تقرير يومي 📊\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💰 *الأسعار:*\n"
            "   • شهر = 50 جنيه\n"
            "   • شهرين = 100 جنيه\n"
            "   • 3 شهور = 120 جنيه\n"
            "   • سنوي = 500 جنيه\n\n"
            "اختار الباقة 👇"
        ),
        "sub_active": (
            "💎 *اشتراكك نشط!*\n\n"
            "📅 ينتهي في: *{date}*\n"
            "⏳ متبقي: *{days}* يوم\n"
            "⏱️ بالثواني: *{secs}* ثانية\n\n"
            "استمتع بكل المميزات 🚀"
        ),
"choose_plan": "اختار الباقة:",
        "choose_payment": "💳 اختار طريقة الدفع:",
        "payment_instructions": (
            "📋 *تعليمات الدفع*\n\n"
            "الباقة: *{plan}*\n"
            "المبلغ: *{price} جنيه*\n"
            "الطريقة: *{method}*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ حول المبلغ على الرقم:\n"
            "   `{number}`\n\n"
            "2️⃣ ابعت صورة الإيصال هنا\n\n"
            "3️⃣ انتظر موافقة الأدمن ✅\n\n"
            "⏰ التفعيل خلال ساعة من الدفع"
        ),
        "payment_numbers": {
            "instapay": "01XXXXXXXXX (InstaPay)",
            "vodafone": "01XXXXXXXXX (Vodafone Cash)",
            "orange":   "01XXXXXXXXX (Orange Cash)",
            "etisalat": "01XXXXXXXXX (Etisalat Cash)",
        },
        "receipt_received": (
            "✅ *تم استلام طلب الدفع!*\n\n"
            "سيتم مراجعته وتفعيل اشتراكك خلال ساعة ⏰\n\n"
            "شكراً لثقتك! 🙏"
        ),
        "sub_success": (
            "🎉 *تم تفعيل اشتراكك!*\n\n"
            "💎 أنت الآن عضو بريميوم\n"
            "📅 ينتهي في: *{date}*\n\n"
            "استمتع بكل المميزات! 🚀"
        ),
        "sub_rejected": (
            "❌ *تم رفض طلب الدفع*\n\n"
            "السبب: {reason}\n\n"
            "تواصل مع الأدمن لمزيد من المعلومات 💬"
        ),
        "free_limit": (
            "⚠️ *وصلت للحد المجاني!*\n\n"
            "النسخة المجانية: 3 منتجات فقط\n\n"
            "💎 اشترك للإضافة بدون حدود!"
        ),
        "premium_only": "💎 هذه الميزة للمشتركين فقط!\n\nاشترك في البريميوم للاستفادة منها.",
        # منتجات
        "send_url": "🔗 أرسل رابط المنتج:",
        "send_selector": (
            "🎯 أرسل CSS Selector للسعر:\n\n"
            "مثال: `span.price`\n\n"
            "إزاي تجيبه؟\n"
            "① افتح الموقع في Chrome\n"
            "② كليك يمين على السعر ← Inspect\n"
            "③ كليك يمين على العنصر\n"
            "④ Copy ← Copy selector"
        ),
        "send_name": "✏️ اكتب اسم للمنتج:",
        "choose_alert": "🔔 اختار نوع التنبيه:",
        "alert_any": "🔔 أي تغيير",
        "alert_percent": "📉 نسبة خصم 💎",
        "alert_target": "🎯 سعر معين 💎",
        "send_percent": "📉 اكتب نسبة الخصم:\nمثال: `30` = لما ينزل 30% أو أكتر",
        "send_target": "🎯 اكتب السعر المطلوب:\nمثال: `100` = لما يوصل 100 أو أقل",
        "checking": "⏳ جاري فحص السعر...",
        "added": "✅ *تمت الإضافة!*\n\n📦 *{name}*\n💰 السعر: *{price}*\n🔔 التنبيه: *{alert}*",
        "alert_desc_any": "أي تغيير",
        "alert_desc_percent": "نزول {val}% أو أكتر",
        "alert_desc_target": "وصول {val} أو أقل",
        "error_url": "❌ الرابط غلط! لازم يبدأ بـ https://",
        "error_price": "❌ مقدرتش أجيب السعر!\nتأكد من الـ Selector.",
        "error_number": "❌ لازم تكتب رقم!\nحاول تاني:",
        "no_products": "📋 مفيش منتجات دلوقتي.\n\nاضغط ➕ لإضافة أول منتج!",
        "products_title": "📋 *منتجاتي:*\n\n",
        "no_change": "✅ مفيش تغييرات\nآخر فحص: {time}",
        "price_down": "📉 *انخفض السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n💰 خصم: *{pct}%* 🎉\n\n🔗 {url}",
        "price_up": "📈 *ارتفع السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n\n🔗 {url}",
        "price_target": "🎯 *وصل للسعر!*\n\n📦 *{name}*\n✅ السعر: *{new}*\n\n🔗 {url}",
        "deleted": "✅ تم الحذف",
        "select_delete": "🗑️ اختار اللي عايز تحذفه:",
        "cancel": "❌ تم الإلغاء",
        "checking_all": "⏳ جاري فحص الأسعار...",
        "send_site_url": "🌐 أرسل رابط صفحة العروض:\nمثال: `https://site.com/sale`",
        "send_site_selector": "🎯 أرسل Selector للأسعار في الصفحة:",
        "send_site_name": "✏️ اكتب اسم للموقع:",
        "site_added": "✅ *تمت إضافة الموقع!*\n\n🌐 *{name}*\n📦 منتجات: *{count}*",
        "site_new_deals": "🔥 *عروض جديدة على {name}!*\n\n{deals}\n\n🔗 {url}",
        # تواصل
        "contact_prompt": "💬 اكتب رسالتك للأدمن وهيرد عليك في أقرب وقت:",
        "contact_sent": "✅ تم إرسال رسالتك للأدمن!\nهيرد عليك قريباً 🙏",
        # تقرير يومي
        "daily_report": "📊 *تقريرك اليومي*\n\n{items}\n\n⏰ {time}",
        "daily_report_item": "📦 *{name}*\n   💰 السعر الحالي: *{price}*\n",
        # إحصائيات
        "stats_text": (
            "📊 *إحصائياتك:*\n\n"
            "📦 منتجات: *{products}*\n"
            "🌐 مواقع: *{sites}*\n"
            "💎 الاشتراك: *{sub}*\n"
            "🕐 آخر فحص: *{last_check}*"
        ),
        "sub_status_free": "🆓 مجاني",
        "sub_status_premium": "💎 بريميوم حتى {date}",
        "help_text": (
            "❓ *دليل الاستخدام:*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "➕ *إضافة منتج:*\n"
            "رابط ← Selector ← اسم ← نوع التنبيه\n\n"
            "🌐 *مراقبة موقع (بريميوم):*\n"
            "رابط صفحة العروض ← Selector ← اسم\n\n"
            "💎 *الاشتراك:*\n"
            "اضغط زرار الاشتراك واتبع الخطوات\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🆓 مجاني: فحص كل ساعة\n"
            "💎 بريميوم: فحص كل 30 دقيقة ⚡"
        ),
        "lang_changed": "✅ تم تغيير اللغة 🇪🇬",
    },
    "en": {
        "welcome": (
            "🎉 *Welcome {name}!*\n\n"
            "Smart Price Tracker Bot 🛒\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🆓 *Free Plan:*\n"
            "   • 3 products only\n"
            "   • Check every hour\n"
            "   • Any change alert\n\n"
            "💎 *Premium Plan:*\n"
            "   • Unlimited products\n"
            "   • Check every 30 min ⚡\n"
            "   • Full site monitoring\n"
            "   • % discount & target alerts\n"
            "   • Daily price report 📊\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Tap any button to start 👇"
        ),
        "main_menu": "🏠 Main Menu",
        "add": "➕ Add Product",
        "watch_site": "🌐 Watch Site 💎",
        "list": "📋 My Products",
        "check": "🔍 Check Prices",
        "subscribe": "💎 Subscribe",
        "dashboard": "📌 Dashboard",
        "delete": "🗑️ Delete",
        "stats": "📊 My Stats",
        "help": "❓ Help",
        "contact": "💬 Contact Admin",
        "language": "🌐 Language",
        "sub_menu": (
            "💎 *Premium Subscription*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✨ *Premium Features:*\n"
            "   ✅ Unlimited products\n"
            "   ✅ Check every 30 min ⚡\n"
            "   ✅ Full site monitoring\n"
            "   ✅ % discount alerts\n"
            "   ✅ Target price alerts\n"
            "   ✅ Daily report 📊\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💰 *Prices:*\n"
            "   • 1 month = 50 EGP\n"
            "   • 2 months = 100 EGP\n"
            "   • 3 months = 120 EGP\n"
            "   • Yearly = 500 EGP\n\n"
            "Choose your plan 👇"
        ),
        "sub_active": "💎 *Active Subscription!*\n\n📅 Expires: *{date}*\n⏳ Days left: *{days}*\n⏱️ Seconds left: *{secs}*\n\nEnjoy! 🚀",

        "choose_plan": "Choose your plan:",
        "choose_payment": "💳 Choose payment method:",
        "payment_instructions": (
            "📋 *Payment Instructions*\n\n"
            "Plan: *{plan}*\n"
            "Amount: *{price} EGP*\n"
            "Method: *{method}*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ Transfer amount to:\n"
            "   `{number}`\n\n"
            "2️⃣ Send receipt screenshot here\n\n"
            "3️⃣ Wait for admin approval ✅\n\n"
            "⏰ Activation within 1 hour"
        ),
        "payment_numbers": {
            "instapay": "01XXXXXXXXX (InstaPay)",
            "vodafone": "01XXXXXXXXX (Vodafone Cash)",
            "orange":   "01XXXXXXXXX (Orange Cash)",
            "etisalat": "01XXXXXXXXX (Etisalat Cash)",
        },
        "receipt_received": "✅ *Payment request received!*\n\nWill be reviewed and activated within 1 hour ⏰\n\nThank you! 🙏",
        "sub_success": "🎉 *Subscription Activated!*\n\n💎 You're now Premium\n📅 Expires: *{date}*\n\nEnjoy! 🚀",
        "sub_rejected": "❌ *Payment Rejected*\n\nReason: {reason}\n\nContact admin for more info 💬",
        "free_limit": "⚠️ *Free limit reached!*\n\nFree plan: 3 products only.\n\n💎 Subscribe for unlimited!",
        "premium_only": "💎 Premium feature only!\n\nSubscribe to access this feature.",
        "send_url": "🔗 Send product URL:",
        "send_selector": "🎯 Send CSS Selector:\n\nExample: `span.price`",
        "send_name": "✏️ Enter product name:",
        "choose_alert": "🔔 Choose alert type:",
        "alert_any": "🔔 Any Change",
        "alert_percent": "📉 % Discount 💎",
        "alert_target": "🎯 Target Price 💎",
        "send_percent": "📉 Enter discount %:\nExample: `30` = alert when drops 30% or more",
        "send_target": "🎯 Enter target price:\nExample: `100` = alert when reaches 100 or less",
        "checking": "⏳ Checking price...",
        "added": "✅ *Added!*\n\n📦 *{name}*\n💰 Price: *{price}*\n🔔 Alert: *{alert}*",
        "alert_desc_any": "Any change",
        "alert_desc_percent": "Drop {val}% or more",
        "alert_desc_target": "Reach {val} or less",
        "error_url": "❌ Invalid URL! Must start with https://",
        "error_price": "❌ Couldn't fetch price!\nCheck the Selector.",
        "error_number": "❌ Enter a valid number!",
        "no_products": "📋 No products yet.\n\nPress ➕ to add your first product!",
        "products_title": "📋 *My Products:*\n\n",
        "no_change": "✅ No changes\nLast check: {time}",
        "price_down": "📉 *Price Dropped!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n💰 Discount: *{pct}%* 🎉\n\n🔗 {url}",
        "price_up": "📈 *Price Increased!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n\n🔗 {url}",
        "price_target": "🎯 *Target Reached!*\n\n📦 *{name}*\n✅ Price: *{new}*\n\n🔗 {url}",
        "deleted": "✅ Deleted",
        "select_delete": "🗑️ Choose to delete:",
        "cancel": "❌ Cancelled",
        "checking_all": "⏳ Checking all prices...",
        "send_site_url": "🌐 Send deals page URL:\nExample: `https://site.com/sale`",
        "send_site_selector": "🎯 Send price Selector:",
        "send_site_name": "✏️ Enter site name:",
        "site_added": "✅ *Site Added!*\n\n🌐 *{name}*\n📦 Products: *{count}*",
        "site_new_deals": "🔥 *New deals on {name}!*\n\n{deals}\n\n🔗 {url}",
        "contact_prompt": "💬 Write your message to admin:",
        "contact_sent": "✅ Message sent to admin!\nThey'll reply soon 🙏",
        "daily_report": "📊 *Your Daily Report*\n\n{items}\n\n⏰ {time}",
        "daily_report_item": "📦 *{name}*\n   💰 Current: *{price}*\n",
        "stats_text": "📊 *Your Stats:*\n\n📦 Products: *{products}*\n🌐 Sites: *{sites}*\n💎 Plan: *{sub}*\n🕐 Last Check: *{last_check}*",
        "sub_status_free": "🆓 Free",
        "sub_status_premium": "💎 Premium until {date}",
        "help_text": "❓ *Usage Guide:*\n\n➕ *Add Product:*\nURL ← Selector ← Name ← Alert type\n\n🌐 *Watch Site (Premium):*\nDeals page URL ← Selector ← Name\n\n💎 *Subscribe:*\nTap Subscribe button\n\n🆓 Free: check hourly\n💎 Premium: check every 30 min ⚡",
        "lang_changed": "✅ Language changed 🇬🇧",
    }
}

# ══════════════════════════════════════════
# الكيبورد
# ══════════════════════════════════════════
def main_keyboard(chat_id):
    lang = get_lang(chat_id)
    txt = T[lang]
    # زرار الاشتراك + الداشبورد تحت زي اللي في الصورة
    return ReplyKeyboardMarkup([
        [KeyboardButton(txt["add"]),      KeyboardButton(txt["watch_site"])],
        [KeyboardButton(txt["list"]),     KeyboardButton(txt["check"])],
        [KeyboardButton(txt["delete"]),   KeyboardButton(txt["stats"])],
        [KeyboardButton(txt["help"]),     KeyboardButton(txt["contact"])],
        [KeyboardButton(txt["language"]), KeyboardButton(txt["subscribe"])],
        [KeyboardButton(txt["dashboard"])],
    ], resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("👥 المستخدمين"), KeyboardButton("💎 المشتركين")],
        [KeyboardButton("📋 طلبات الدفع"), KeyboardButton("➕ اشتراك يدوي")],
        [KeyboardButton("📢 رسالة للكل"),  KeyboardButton("📊 إحصائيات")],
        [KeyboardButton("🏠 رجوع")],
    ], resize_keyboard=True)

# ══════════════════════════════════════════
# جلب الأسعار
# ══════════════════════════════════════════
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
        return [
            re.search(r"[\d,،.]+", el.get_text().strip().replace("\xa0", " ")).group(0)
            for el in soup.select(selector)[:20]
            if re.search(r"[\d,،.]+", el.get_text().strip().replace("\xa0", " "))
        ]
    except:
        return []

# ══════════════════════════════════════════
# Start & اللغة
# ══════════════════════════════════════════
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = get_name(update.effective_user)
    data = load_data()
    cid = str(chat_id)
    if cid not in data:
        data[cid] = {"joined": datetime.now().isoformat(), "name": name}
        save_data(data)
    lang = get_lang(chat_id)
    if data.get(cid, {}).get("lang"):
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

async def change_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "اختار اللغة / Choose language:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇪🇬 عربي", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")
        ]])
    )

# ══════════════════════════════════════════
# نظام الاشتراك
# ══════════════════════════════════════════
async def subscribe_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_premium(chat_id):
        data = load_data()
        sub = data[str(chat_id)]["subscription"]
        expiry = datetime.fromisoformat(sub["expiry"])
        days = (expiry - datetime.now()).days
        secs = remaining_seconds(chat_id)
        await update.message.reply_text(
            t(chat_id, "sub_active", date=expiry.strftime("%Y-%m-%d"), days=days, secs=secs),
            parse_mode="Markdown",
            reply_markup=main_keyboard(chat_id)
        )
        return

    await update.message.reply_text(
        t(chat_id, "sub_menu"),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📅 {PLANS['1']['label']}", callback_data="plan_1")],
            [InlineKeyboardButton(f"📅 {PLANS['2']['label']}", callback_data="plan_2")],
            [InlineKeyboardButton(f"📅 {PLANS['3']['label']}", callback_data="plan_3")],
            [InlineKeyboardButton(f"🌟 {PLANS['12']['label']}", callback_data="plan_12")],
        ])
    )

async def plan_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    plan_key = query.data.replace("plan_", "")
    plan = PLANS[plan_key]
    ctx.user_data["selected_plan"] = plan_key
    await query.answer()
    lang = get_lang(chat_id)
    await query.edit_message_text(
        t(chat_id, "choose_payment"),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(v, callback_data=f"pay_{k}")]
            for k, v in PAYMENT_METHODS.items()
        ])
    )

async def payment_method_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    method_key = query.data.replace("pay_", "")
    plan_key = ctx.user_data.get("selected_plan", "1")
    plan = PLANS[plan_key]
    lang = get_lang(chat_id)
    number = T[lang]["payment_numbers"][method_key]
    method_name = PAYMENT_METHODS[method_key]

    await query.edit_message_text(
        t(chat_id, "payment_instructions",
          plan=plan["label"], price=plan["price"],
          method=method_name, number=number),
        parse_mode="Markdown"
    )
    ctx.user_data["awaiting_receipt"] = True
    ctx.user_data["payment_plan"] = plan_key
    ctx.user_data["payment_method"] = method_key

async def handle_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not ctx.user_data.get("awaiting_receipt"):
        return False

    plan_key = ctx.user_data.get("payment_plan", "1")
    plan = PLANS[plan_key]
    method_key = ctx.user_data.get("payment_method", "instapay")
    method_name = PAYMENT_METHODS[method_key]
    name = get_name(update.effective_user)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # احفظ الطلب
    data = load_data()
    cid = str(chat_id)
    if "pending_payments" not in data:
        data["pending_payments"] = {}
    req_id = f"{chat_id}_{int(datetime.now().timestamp())}"
    data["pending_payments"][req_id] = {
        "chat_id": chat_id,
        "name": name,
        "plan": plan_key,
        "months": plan["months"],
        "price": plan["price"],
        "method": method_name,
        "time": now,
        "status": "pending"
    }
    save_data(data)

    # بعت إشعار للأدمن
    if ADMIN_ID:
        msg = (
            f"💳 *طلب دفع جديد!*\n\n"
            f"👤 الاسم: *{name}*\n"
            f"🆔 ID: `{chat_id}`\n"
            f"📅 الباقة: *{plan['label']}*\n"
            f"💰 المبلغ: *{plan['price']} جنيه*\n"
            f"💳 الطريقة: *{method_name}*\n"
            f"🕐 الوقت: {now}"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{req_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject_{req_id}"),
            ]
        ])
        if update.message.photo:
            await ctx.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=update.message.photo[-1].file_id,
                caption=msg,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await ctx.bot.send_message(
                chat_id=ADMIN_ID,
                text=msg,
                parse_mode="Markdown",
                reply_markup=keyboard
            )

    ctx.user_data["awaiting_receipt"] = False
    await update.message.reply_text(
        t(chat_id, "receipt_received"),
        parse_mode="Markdown",
        reply_markup=main_keyboard(chat_id)
    )
    return True

async def approve_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("مش أدمن!", show_alert=True)
        return

    req_id = query.data.replace("approve_", "")
    data = load_data()
    req = data.get("pending_payments", {}).get(req_id)
    if not req:
        await query.answer("الطلب مش موجود!", show_alert=True)
        return

    user_id = req["chat_id"]
    months = req["months"]
    expiry_date = add_subscription(user_id, months)

    data["pending_payments"][req_id]["status"] = "approved"
    save_data(data)

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"✅ تم تفعيل اشتراك {req['name']} ({user_id}) لمدة {months} شهر")

    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=t(user_id, "sub_success", date=expiry_date),
            parse_mode="Markdown",
            reply_markup=main_keyboard(user_id)
        )
    except Exception as e:
        logger.error(f"notify user: {e}")

async def reject_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("مش أدمن!", show_alert=True)
        return

    req_id = query.data.replace("reject_", "")
    data = load_data()
    req = data.get("pending_payments", {}).get(req_id)
    if not req:
        await query.answer("الطلب مش موجود!", show_alert=True)
        return

    user_id = req["chat_id"]
    data["pending_payments"][req_id]["status"] = "rejected"
    save_data(data)

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"❌ تم رفض طلب {req['name']} ({user_id})")

    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=t(user_id, "sub_rejected", reason="يرجى التواصل مع الأدمن"),
            parse_mode="Markdown",
            reply_markup=main_keyboard(user_id)
        )
    except Exception as e:
        logger.error(f"notify user: {e}")

# ══════════════════════════════════════════
# إضافة منتج
# ══════════════════════════════════════════
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
    lang = get_lang(chat_id)
    premium = is_premium(chat_id)
    keyboard = [[InlineKeyboardButton(T[lang]["alert_any"], callback_data="alert_any")]]
    if premium:
        keyboard.append([
            InlineKeyboardButton(T[lang]["alert_percent"], callback_data="alert_percent"),
            InlineKeyboardButton(T[lang]["alert_target"],  callback_data="alert_target"),
        ])
    else:
        keyboard.append([InlineKeyboardButton("🔒 نسبة خصم (بريميوم)", callback_data="alert_locked")])
        keyboard.append([InlineKeyboardButton("🔒 سعر معين (بريميوم)", callback_data="alert_locked")])
    await update.message.reply_text(
        t(chat_id, "choose_alert"),
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
    lang = get_lang(chat_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

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

# ══════════════════════════════════════════
# مراقبة موقع
# ══════════════════════════════════════════
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
    await update.message.reply_text(t(update.effective_chat.id, "cancel"), reply_markup=main_keyboard(update.effective_chat.id))
    return ConversationHandler.END

# ══════════════════════════════════════════
# عرض / فحص / حذف
# ══════════════════════════════════════════
async def list_products(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})
    sites = data.get(chat_id, {}).get("sites", {})
    if not products and not sites:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return
    lang = get_lang(int(chat_id))
    text = ""
    if products:
        text += T[lang]["products_title"]
        for pid, p in products.items():
            icon = {"any": "🔔", "percent": "📉", "target": "🎯"}.get(p.get("alert_type"), "🔔")
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
    changed = 0
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
            changed += 1
            pct = round((old_f - new_f) / old_f * 100) if old_f and new_f else 0
            await update.message.reply_text(
                t(int(chat_id), msg_key, name=p["name"], old=p["price"], new=new_price, pct=pct, url=p["url"]),
                parse_mode="Markdown"
            )
    data[chat_id]["last_check"] = now
    save_data(data)
    if changed == 0:
        await update.message.reply_text(t(int(chat_id), "no_change", time=now), reply_markup=main_keyboard(int(chat_id)))

async def delete_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    products = data.get(chat_id, {}).get("products", {})
    sites = data.get(chat_id, {}).get("sites", {})
    if not products and not sites:
        await update.message.reply_text(t(int(chat_id), "no_products"), reply_markup=main_keyboard(int(chat_id)))
        return
    keyboard = (
        [[InlineKeyboardButton(f"📦 {p['name']} — {p['price']}", callback_data=f"del_p_{pid}")] for pid, p in products.items()] +
        [[InlineKeyboardButton(f"🌐 {s['name']}", callback_data=f"del_s_{sid}")] for sid, s in sites.items()]
    )
    await update.message.reply_text(t(int(chat_id), "select_delete"), reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    data = load_data()
    if query.data.startswith("del_p_"):
        data.get(chat_id, {}).get("products", {}).pop(query.data.replace("del_p_", ""), None)
    elif query.data.startswith("del_s_"):
        data.get(chat_id, {}).get("sites", {}).pop(query.data.replace("del_s_", ""), None)
    save_data(data)
    await query.edit_message_text(t(int(chat_id), "deleted"))
    await ctx.bot.send_message(chat_id=int(chat_id), text=t(int(chat_id), "main_menu"), parse_mode="Markdown", reply_markup=main_keyboard(int(chat_id)))

# ══════════════════════════════════════════
# التواصل مع الأدمن
# ══════════════════════════════════════════
async def contact_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "contact_prompt"))
    return WAITING_CONTACT_MSG

async def contact_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = get_name(update.effective_user)
    msg_text = update.message.text
    if ADMIN_ID:
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💬 *رسالة من مستخدم*\n\n👤 {name}\n🆔 `{chat_id}`\n\n📝 {msg_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ رد", callback_data=f"reply_{chat_id}")
            ]])
        )
    await update.message.reply_text(t(chat_id, "contact_sent"), reply_markup=main_keyboard(chat_id))
    return ConversationHandler.END

async def contact_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(t(chat_id, "cancel"), reply_markup=main_keyboard(chat_id))
    return ConversationHandler.END

# ══════════════════════════════════════════
# لوحة الأدمن
# ══════════════════════════════════════════
async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        return
    data = load_data()
    users = [k for k in data.keys() if k.isdigit()]
    premium_users = [k for k in users if is_premium(int(k))]
    pending = [v for v in data.get("pending_payments", {}).values() if v["status"] == "pending"]
    await update.message.reply_text(
        f"👑 *لوحة الأدمن*\n\n"
        f"👥 إجمالي المستخدمين: *{len(users)}*\n"
        f"💎 المشتركين النشطين: *{len(premium_users)}*\n"
        f"🆓 المجانيين: *{len(users) - len(premium_users)}*\n"
        f"⏳ طلبات دفع معلقة: *{len(pending)}*",
        parse_mode="Markdown",
        reply_markup=admin_keyboard()
    )

async def admin_show_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return
    data = load_data()
    users = [(k, v) for k, v in data.items() if k.isdigit()]
    if not users:
        await update.message.reply_text("مفيش مستخدمين")
        return
    text = "👥 *كل المستخدمين:*\n\n"
    for uid, udata in users[:30]:
        plan = "💎" if is_premium(int(uid)) else "🆓"
        name = udata.get("name", "—")
        products = len(udata.get("products", {}))
        text += f"{plan} *{name}* (`{uid}`)\n   📦 منتجات: {products}\n\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())

async def admin_show_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return
    data = load_data()
    premium_users = [(k, v) for k, v in data.items() if k.isdigit() and is_premium(int(k))]
    if not premium_users:
        await update.message.reply_text("مفيش مشتركين دلوقتي")
        return
    text = "💎 *المشتركين النشطين:*\n\n"
    for uid, udata in premium_users:
        name = udata.get("name", "—")
        sub = udata.get("subscription", {})
        expiry = datetime.fromisoformat(sub["expiry"]).strftime("%Y-%m-%d") if sub else "—"
        days = (datetime.fromisoformat(sub["expiry"]) - datetime.now()).days if sub else 0
        text += f"💎 *{name}* (`{uid}`)\n   📅 ينتهي: {expiry} ({days} يوم)\n\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())

async def admin_show_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return
    data = load_data()
    pending = [(k, v) for k, v in data.get("pending_payments", {}).items() if v["status"] == "pending"]
    if not pending:
        await update.message.reply_text("✅ مفيش طلبات معلقة")
        return
    for req_id, req in pending:
        await update.message.reply_text(
            f"💳 *طلب دفع*\n\n"
            f"👤 {req['name']} (`{req['chat_id']}`)\n"
            f"📅 {req['plan']} شهر\n"
            f"💰 {req['price']} جنيه\n"
            f"💳 {req['method']}\n"
            f"🕐 {req['time']}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{req_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject_{req_id}"),
            ]])
        )

async def admin_add_sub_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("أرسل ID المستخدم:")
    return WAITING_ADMIN_SUB_ID

async def admin_add_sub_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["manual_uid"] = int(update.message.text.strip())
        await update.message.reply_text("أرسل عدد الأشهر (مثال: 1 أو 3 أو 12):")
        return WAITING_ADMIN_SUB_MONTHS
    except:
        await update.message.reply_text("❌ ID غلط!")
        return WAITING_ADMIN_SUB_ID

async def admin_add_sub_months(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        months = int(update.message.text.strip())
        uid = ctx.user_data["manual_uid"]
        expiry_date = add_subscription(uid, months)
        await update.message.reply_text(
            f"✅ تم تفعيل اشتراك `{uid}` لمدة {months} شهر حتى {expiry_date}",
            reply_markup=admin_keyboard()
        )
        try:
            await ctx.bot.send_message(
                chat_id=uid,
                text=t(uid, "sub_success", date=expiry_date),
                parse_mode="Markdown",
                reply_markup=main_keyboard(uid)
            )
        except:
            pass
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
    return ConversationHandler.END

async def admin_broadcast_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("📢 اكتب الرسالة اللي عايز تبعتها لكل المستخدمين:")
    return WAITING_BROADCAST

async def admin_broadcast_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return ConversationHandler.END
    msg = update.message.text
    data = load_data()
    users = [k for k in data.keys() if k.isdigit()]
    sent = 0
    for uid in users:
        try:
            await ctx.bot.send_message(chat_id=int(uid), text=msg, parse_mode="Markdown")
            sent += 1
        except:
            pass
    await update.message.reply_text(f"✅ تم الإرسال لـ {sent} مستخدم", reply_markup=admin_keyboard())
    return ConversationHandler.END



# ══════════════════════════════════════════
# داشبورد المستخدم
# ══════════════════════════════════════════
async def dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = load_data()
    u = data.get(str(chat_id), {})
    name = u.get("name") or get_name(update.effective_user)
    products = len(u.get("products", {}))
    sites = len(u.get("sites", {}))
    lang = get_lang(chat_id)

    if is_premium(chat_id):
        expiry = get_subscription_expiry(chat_id)
        sec = remaining_seconds(chat_id)
        human, total = format_remaining_seconds(sec)
        expiry_s = expiry.strftime("%Y-%m-%d %H:%M") if expiry else "—"
        plan_line = f"💎 بريميوم\n📅 ينتهي: *{expiry_s}*\n⏱️ متبقي: *{human}*\n🔢 بالثواني: *{total}*"
    else:
        plan_line = f"🆓 مجاني (حد {FREE_LIMIT} منتجات)\n💎 اشترك عشان تفتح كل المميزات"

    if lang == "ar":
        text = (
            f"📌 *داشبوردك يا {name}*\n\n"
            f"📦 منتجات: *{products}*\n"
            f"🌐 مواقع: *{sites}*\n"
            f"{plan_line}\n\n"
            "اختر زرار من تحت 👇"
        )
    else:
        expiry = get_subscription_expiry(chat_id)
        sec = remaining_seconds(chat_id)
        human, total = format_remaining_seconds(sec)
        expiry_s = expiry.strftime("%Y-%m-%d %H:%M") if expiry else "—"
        plan_line_en = (
            f"💎 Premium\nExpires: *{expiry_s}*\nTime left: *{human}*\nSeconds: *{total}*"
            if is_premium(chat_id) else f"🆓 Free (limit {FREE_LIMIT} products)\nSubscribe for full access"
        )
        text = (
            f"📌 *Your Dashboard, {name}*\n\n"
            f"📦 Products: *{products}*\n"
            f"🌐 Sites: *{sites}*\n"
            f"{plan_line_en}\n\n"
            "Use the buttons below 👇"
        )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard(chat_id))

# ══════════════════════════════════════════
# أوامر الأدمن (Status سريع)
# ══════════════════════════════════════════
async def admin_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        return
    data = load_data()
    users = [k for k in data.keys() if k.isdigit()]
    premium_users = [k for k in users if is_premium(int(k))]
    pending = [v for v in data.get("pending_payments", {}).values() if v["status"] == "pending"]
    await update.message.reply_text(
        f"🧾 *Status*\n\n"
        f"👥 Users: *{len(users)}*\n"
        f"💎 Premium: *{len(premium_users)}*\n"
        f"🆓 Free: *{len(users) - len(premium_users)}*\n"
        f"⏳ Pending payments: *{len(pending)}*",
        parse_mode="Markdown",
        reply_markup=admin_keyboard()
    )
# ══════════════════════════════════════════
# إحصائيات ومساعدة
# ══════════════════════════════════════════
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    user_data = data.get(chat_id, {})
    products = len(user_data.get("products", {}))
    sites = len(user_data.get("sites", {}))
    last_check = user_data.get("last_check", "—")
    lang = get_lang(int(chat_id))
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

# ══════════════════════════════════════════
# الفحص التلقائي
# ══════════════════════════════════════════
async def auto_check_free(ctx: ContextTypes.DEFAULT_TYPE):
    await _do_check(ctx, premium_only=False)

async def auto_check_premium(ctx: ContextTypes.DEFAULT_TYPE):
    await _do_check(ctx, premium_only=True)

async def _do_check(ctx, premium_only=False):
    data = load_data()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for chat_id, user_data in data.items():
        if not chat_id.isdigit():
            continue
        user_is_premium = is_premium(int(chat_id))
        if premium_only and not user_is_premium:
            continue
        if not premium_only and user_is_premium:
            continue
        for pid, p in user_data.get("products", {}).items():
            new_price = await fetch_price(p["url"], p["selector"])
            if not new_price or new_price == p["price"]:
                continue
            old_f, new_f = price_to_float(p["price"]), price_to_float(new_price)
            alert_type = p.get("alert_type", "any")
            alert_value = p.get("alert_value")
            should_notify, msg_key = False, "price_down"
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

async def daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for chat_id, user_data in data.items():
        if not chat_id.isdigit() or not is_premium(int(chat_id)):
            continue
        products = user_data.get("products", {})
        if not products:
            continue
        lang = get_lang(int(chat_id))
        items = ""
        for p in products.values():
            items += T[lang]["daily_report_item"].format(name=p["name"], price=p["price"])
        try:
            await ctx.bot.send_message(
                chat_id=int(chat_id),
                text=T[lang]["daily_report"].format(items=items, time=now),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"daily report: {e}")

# ══════════════════════════════════════════
# معالج الأزرار والصور
# ══════════════════════════════════════════
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    lang = get_lang(chat_id)
    txt = T[lang]

    # أزرار الأدمن
    if chat_id == ADMIN_ID:
        if text == "👥 المستخدمين":    await admin_show_users(update, ctx); return
        if text == "💎 المشتركين":     await admin_show_premium(update, ctx); return
        if text == "📋 طلبات الدفع":  await admin_show_pending(update, ctx); return
        if text == "📊 إحصائيات":      await admin_panel(update, ctx); return
        if text == "🏠 رجوع":
            await update.message.reply_text(txt["main_menu"], reply_markup=main_keyboard(chat_id)); return

    if text == txt["list"]:         await list_products(update, ctx)
    elif text == txt["check"]:      await check_prices(update, ctx)
    elif text == txt["delete"]:     await delete_menu(update, ctx)
    elif text == txt["language"]:   await change_language(update, ctx)
    elif text == txt["stats"]:      await stats(update, ctx)
    elif text == txt["help"]:       await help_cmd(update, ctx)
    elif text == txt["subscribe"]:  await subscribe_menu(update, ctx)
    elif text == txt["dashboard"]:  await dashboard(update, ctx)

async def photo_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await handle_receipt(update, ctx)

async def open_sub_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    lang = get_lang(chat_id)
    await ctx.bot.send_message(
        chat_id=chat_id,
        text=T[lang]["sub_menu"],
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📅 {PLANS['1']['label']}", callback_data="plan_1")],
            [InlineKeyboardButton(f"📅 {PLANS['2']['label']}", callback_data="plan_2")],
            [InlineKeyboardButton(f"📅 {PLANS['3']['label']}", callback_data="plan_3")],
            [InlineKeyboardButton(f"🌟 {PLANS['12']['label']}", callback_data="plan_12")],
        ])
    )

# ══════════════════════════════════════════
# تشغيل البوت
# ══════════════════════════════════════════
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

    contact_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^(💬 تواصل مع الأدمن|💬 Contact Admin)$"), contact_start),
        ],
        states={
            WAITING_CONTACT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_send)],
        },
        fallbacks=[CommandHandler("cancel", contact_cancel)],
    )

    admin_sub_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ اشتراك يدوي$"), admin_add_sub_start)],
        states={
            WAITING_ADMIN_SUB_ID:     [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_sub_id)],
            WAITING_ADMIN_SUB_MONTHS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_sub_months)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    broadcast_conv = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast", admin_broadcast_start),
            MessageHandler(filters.Regex(r"^📢 رسالة للكل$"), admin_broadcast_start)
        ],
        states={
            WAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("status", admin_status))
    app.add_handler(CommandHandler("plan", subscribe_menu))
    app.add_handler(CommandHandler("my", list_products))
    app.add_handler(CommandHandler("check", check_prices))
    app.add_handler(CommandHandler("delete", delete_menu))

    app.add_handler(CallbackQueryHandler(lang_callback,          pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(plan_callback,          pattern=r"^plan_"))
    app.add_handler(CallbackQueryHandler(payment_method_callback,pattern=r"^pay_"))
    app.add_handler(CallbackQueryHandler(approve_payment,        pattern=r"^approve_"))
    app.add_handler(CallbackQueryHandler(reject_payment,         pattern=r"^reject_"))
    app.add_handler(CallbackQueryHandler(delete_callback,        pattern=r"^del_"))
    app.add_handler(CallbackQueryHandler(open_sub_callback,      pattern=r"^open_sub$"))
    app.add_handler(add_conv)
    app.add_handler(site_conv)
    app.add_handler(contact_conv)
    app.add_handler(admin_sub_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    # فحص المجانيين كل ساعة
    app.job_queue.run_repeating(auto_check_free,    interval=CHECK_FREE,    first=60)
    # فحص البريميوم كل نص ساعة
    app.job_queue.run_repeating(auto_check_premium, interval=CHECK_PREMIUM, first=30)
    # تقرير يومي الساعة 9 الصبح
    app.job_queue.run_daily(daily_report, time=datetime.strptime("09:00", "%H:%M").time())

    logger.info("✅ البوت شغال!")
    app.run_polling()

if __name__ == "__main__":
    main()
