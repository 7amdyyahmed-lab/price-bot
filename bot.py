#!/usr/bin/env python3
import json, os, re, logging, random, string
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "0"))
DATA_FILE = "data.json"

CHECK_INTERVALS = {"free":86400,"regular":43200,"pro":21600,"premium":10800,"ultra":1800}
PRODUCT_LIMITS  = {"free":3,"regular":10,"pro":20,"premium":30,"ultra":50}
SITE_LIMITS     = {"free":0,"regular":0,"pro":1,"premium":3,"ultra":10}
PLAN_FEATURES   = {
    "free":    {"alerts":["any"],                    "daily":False,"weekly":False,"history":False},
    "regular": {"alerts":["any","percent","target"], "daily":False,"weekly":False,"history":True},
    "pro":     {"alerts":["any","percent","target"], "daily":True, "weekly":False,"history":True},
    "premium": {"alerts":["any","percent","target"], "daily":True, "weekly":True, "history":True},
    "ultra":   {"alerts":["any","percent","target"], "daily":True, "weekly":True, "history":True},
}
PLANS = {
    "regular": {"name":"🔵 Regular","prices":{"1":60,"3":160,"12":600},"labels":{"1":"شهر — 60ج","3":"3 شهور — 160ج","12":"سنوي — 600ج"}},
    "pro":     {"name":"🟢 Pro",    "prices":{"1":120,"3":300,"12":1200},"labels":{"1":"شهر — 120ج","3":"3 شهور — 300ج","12":"سنوي — 1,200ج"}},
    "premium": {"name":"🟣 Premium","prices":{"1":200,"3":500,"12":1800},"labels":{"1":"شهر — 200ج","3":"3 شهور — 500ج","12":"سنوي — 1,800ج"}},
    "ultra":   {"name":"⭐ Ultra",  "prices":{"1":400,"3":1000,"12":3600},"labels":{"1":"شهر — 400ج","3":"3 شهور — 1,000ج","12":"سنوي — 3,600ج"}},
}
PAYMENT_METHODS = {
    "instapay": {"name":"💳 InstaPay",       "number":"01XXXXXXXXX"},
    "vodafone": {"name":"📱 Vodafone Cash",  "number":"01XXXXXXXXX"},
    "orange":   {"name":"🟠 Orange Cash",   "number":"01XXXXXXXXX"},
    "etisalat": {"name":"🔵 Etisalat Cash", "number":"01XXXXXXXXX"},
    "binance":  {"name":"🟡 Binance Pay",   "number":"BINANCE_ID_HERE"},
    "onchain":  {"name":"🔗 On-Chain USDT", "number":"0xYOUR_WALLET_HERE"},
}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

S_URL,S_SEL,S_NAME,S_ALERT,S_AVAL = range(5)
S_SURL,S_SSEL,S_SNAME = range(5,8)
S_CONTACT = 8
S_ASUB_ID,S_ASUB_PLAN,S_ASUB_MON = range(9,12)
S_BROADCAST,S_CODE = 12,13

def load():
    if Path(DATA_FILE).exists():
        with open(DATA_FILE,"r",encoding="utf-8") as f: return json.load(f)
    return {}

def save(data):
    with open(DATA_FILE,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)

def uget(uid): return load().get(str(uid),{})

def usave(uid,udata):
    data=load(); data[str(uid)]=udata; save(data)

def get_plan(uid):
    sub=uget(uid).get("subscription")
    if not sub: return "free"
    try:
        if datetime.fromisoformat(sub["expiry"])>datetime.now(): return sub["plan"]
    except: pass
    return "free"

def get_lang(uid): return uget(uid).get("lang","ar")
def get_cur(uid):  return uget(uid).get("currency","EGP")

def activate_sub(uid,plan,months):
    udata=uget(uid); now=datetime.now()
    sub=udata.get("subscription")
    if sub:
        try:
            base=datetime.fromisoformat(sub["expiry"])
            expiry=(base if base>now else now)+timedelta(days=30*months)
        except: expiry=now+timedelta(days=30*months)
    else: expiry=now+timedelta(days=30*months)
    udata["subscription"]={"plan":plan,"expiry":expiry.isoformat(),"activated":now.isoformat(),"months":months}
    usave(uid,udata)
    return expiry.strftime("%Y-%m-%d")

def gen_code():
    return "".join(random.choices(string.ascii_uppercase+string.digits,k=10))

def now_str(): return datetime.now().strftime("%Y-%m-%d %H:%M")
def p2f(s):
    try: return float(re.sub(r"[^\d.]","",str(s).replace(",",".")))
    except: return None

def plan_lbl(p,lang="ar"):
    m={"free":"🆓 Free","regular":"🔵 Regular","pro":"🟢 Pro","premium":"🟣 Premium","ultra":"⭐ Ultra"}
    return m.get(p,p)

def interval_lbl(p,lang="ar"):
    m={"ar":{"free":"24 ساعة","regular":"12 ساعة","pro":"6 ساعات","premium":"3 ساعات","ultra":"30 دقيقة"},
       "en":{"free":"24h","regular":"12h","pro":"6h","premium":"3h","ultra":"30min"}}
    return m[lang].get(p,"—")

HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Accept-Language":"ar,en;q=0.9"}

async def fetch_price(url,sel):
    try:
        async with httpx.AsyncClient(headers=HEADERS,follow_redirects=True,timeout=15) as c:
            r=await c.get(url); r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        el=soup.select_one(sel)
        if el:
            tx=el.get_text().strip().replace("\xa0"," ")
            m=re.search(r"[\d,،.]+",tx)
            return m.group(0) if m else tx[:50]
    except Exception as e: logger.warning(f"fetch: {e}")
    return None

async def fetch_all(url,sel):
    try:
        async with httpx.AsyncClient(headers=HEADERS,follow_redirects=True,timeout=15) as c:
            r=await c.get(url); r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        res=[]
        for el in soup.select(sel)[:20]:
            tx=el.get_text().strip().replace("\xa0"," ")
            m=re.search(r"[\d,،.]+",tx)
            if m: res.append(m.group(0))
        return res
    except: return []

def main_kb(uid):
    lang=get_lang(uid)
    if lang=="ar":
        return ReplyKeyboardMarkup([
            ["➕ إضافة منتج","🌐 مراقبة موقع"],
            ["📋 منتجاتي","🔍 فحص الآن"],
            ["📈 تاريخ الأسعار","📊 لوحتي"],
            ["💬 تواصل","❓ مساعدة"],
            ["💱 العملة","🌐 اللغة"],
            ["💎 الاشتراك"],
        ],resize_keyboard=True)
    else:
        return ReplyKeyboardMarkup([
            ["➕ Add Product","🌐 Watch Site"],
            ["📋 My Products","🔍 Check Now"],
            ["📈 Price History","📊 Dashboard"],
            ["💬 Contact","❓ Help"],
            ["💱 Currency","🌐 Language"],
            ["💎 Subscribe"],
        ],resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        ["👥 المستخدمين","💎 المشتركين"],
        ["📋 طلبات الدفع","🎁 الأكواد"],
        ["📊 الإيرادات","➕ اشتراك يدوي"],
        ["📢 رسالة للكل","🏠 رجوع"],
    ],resize_keyboard=True)

def t(uid,key,**kw):
    lang=get_lang(uid)
    text=TX[lang].get(key,key)
    return text.format(**kw) if kw else text

TX={
"ar":{
"welcome":"🎉 *أهلاً {name}!*\n\nبوت تتبع الأسعار الذكي 🛒\n\n━━━━━━━━━━━━━━━━━━\n🆓 *Free:* 3 منتجات، فحص يومي\n🔵 *Regular:* 10 منتجات، كل 12 ساعة\n🟢 *Pro:* 20 منتج، كل 6 ساعات + تقرير\n🟣 *Premium:* 30 منتج، كل 3 ساعات\n⭐ *Ultra:* 50 منتج، كل 30 دقيقة\n\n━━━━━━━━━━━━━━━━━━\nاضغط أي زرار للبدء 👇",
"sub_menu":"💎 *الاشتراك*\n\n🆓 Free: 3 منتجات - فحص 24h\n🔵 Regular: 10 منتجات - 12h - 60ج/شهر\n🟢 Pro: 20 منتج - 6h - تقرير - 120ج/شهر\n🟣 Premium: 30 منتج - 3h - تقارير - 200ج/شهر\n⭐ Ultra: 50 منتج - 30min - كل المميزات - 400ج/شهر\n\nاختار الباقة 👇",
"sub_active":"💎 *اشتراكك النشط:*\n\n📦 الباقة: *{plan}*\n📅 ينتهي: *{date}*\n⏳ متبقي: *{days}* يوم\n⚡ فحص كل: *{interval}*\n📦 منتجات: *{prods}/{limit}*",
"choose_dur":"اختار مدة الاشتراك:",
"choose_pay":"💳 اختار طريقة الدفع:",
"pay_info":"📋 *تفاصيل الدفع*\n\n📦 الباقة: *{plan}*\n⏱ المدة: *{months}*\n💰 المبلغ: *{price} جنيه*\n💳 الطريقة: *{method}*\n\n━━━━━━━━━━━━━━━━━━\n1️⃣ حول المبلغ على:\n`{number}`\n\n2️⃣ ابعت صورة الإيصال هنا 📸\n\n3️⃣ انتظر موافقة الأدمن ✅\n\n⏰ التفعيل خلال ساعة",
"receipt_ok":"✅ *تم استلام طلبك!*\n\nهيتم التفعيل خلال ساعة ⏰\nشكراً 🙏",
"approved":"🎉 *تم تفعيل اشتراكك!*\n\n📦 الباقة: *{plan}*\n📅 ينتهي: *{date}*\n⚡ فحص كل: *{interval}*\n\nاستمتع بكل المميزات! 🚀",
"rejected":"❌ *تم رفض الطلب*\n\nالسبب: {reason}\n\nتواصل مع الأدمن 💬",
"invoice":"🧾 *فاتورتك*\n\n📦 الباقة: *{plan}*\n⏱ المدة: *{months}* شهر\n💰 المبلغ: *{price} جنيه*\n💳 الطريقة: *{method}*\n📅 ينتهي: *{expiry}*\n🕐 {time}",
"expiry_warn":"⚠️ *تنبيه!*\n\nاشتراكك ينتهي خلال *{days}* أيام!\n📅 تاريخ الانتهاء: *{date}*\n\nجدد الاشتراك للاستمرار 💎",
"expired":"⚠️ انتهى اشتراكك!\n\nتم تحويلك للنسخة المجانية 🆓\nجدد للاستمرار في كل المميزات 💎",
"send_url":"🔗 أرسل رابط المنتج:",
"send_sel":"🎯 أرسل CSS Selector للسعر:\n\nمثال: `span.price`\n\nإزاي تجيبه؟\n① كليك يمين على السعر\n② Inspect\n③ كليك يمين على العنصر\n④ Copy ← Copy selector",
"send_name":"✏️ اكتب اسم للمنتج:",
"choose_alert":"🔔 اختار نوع التنبيه:",
"send_pct":"📉 اكتب نسبة الخصم:\nمثال: `30` = لما ينزل 30% أو أكتر",
"send_tgt":"🎯 اكتب السعر المطلوب:\nمثال: `100` = لما يوصل 100 أو أقل",
"checking":"⏳ جاري فحص السعر...",
"added":"✅ *تمت الإضافة!*\n\n📦 *{name}*\n💰 السعر: *{price}*\n🔔 التنبيه: *{alert}*\n🕐 {time}",
"al_any":"أي تغيير","al_pct":"نزول {v}% أو أكتر","al_tgt":"وصول {v} أو أقل",
"err_url":"❌ الرابط غلط! لازم يبدأ بـ https://",
"err_price":"❌ مقدرتش أجيب السعر!\nتأكد من الـ Selector.",
"err_num":"❌ لازم تكتب رقم صح!",
"limit":"⚠️ *وصلت للحد!*\n\nباقتك ({plan}) تسمح بـ {limit} منتج فقط.\n\n💎 اترقيلى باقة أعلى!",
"no_prods":"📋 مفيش منتجات دلوقتي.\n\nاضغط ➕ لإضافة أول منتج!",
"checking_all":"⏳ جاري فحص الأسعار...",
"no_change":"✅ مفيش تغييرات\n⏰ {time}",
"price_down":"📉 *انخفض السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n💰 خصم: *{pct}%* 🎉\n\n🔗 {url}",
"price_up":"📈 *ارتفع السعر!*\n\n📦 *{name}*\n❌ كان: *{old}*\n✅ بقى: *{new}*\n\n🔗 {url}",
"price_tgt":"🎯 *وصل للسعر!*\n\n📦 *{name}*\n✅ السعر: *{new}*\n\n🔗 {url}",
"deleted":"✅ تم الحذف",
"sel_delete":"🗑️ اختار اللي عايز تحذفه:",
"cancel":"❌ تم الإلغاء",
"site_need_plan":"⚠️ مراقبة الموقع متاحة من باقة *Pro* فأعلى",
"site_limit":"⚠️ باقتك تسمح بـ {limit} موقع فقط",
"send_surl":"🌐 أرسل رابط صفحة العروض:\nمثال: `https://site.com/sale`",
"send_ssel":"🎯 أرسل Selector للأسعار في الصفحة:",
"send_sname":"✏️ اكتب اسم للموقع:",
"site_added":"✅ *تمت إضافة الموقع!*\n🌐 *{name}*\n📦 منتجات موجودة: *{count}*",
"site_deals":"🔥 *عروض جديدة على {name}!*\n\n{deals}\n\n🔗 {url}",
"hist_need":"📈 تاريخ الأسعار متاح من باقة *Regular* فأعلى",
"hist_title":"📈 *تاريخ أسعار {name}:*\n\n",
"hist_item":"📅 {date}: *{price}*\n",
"no_hist":"📈 مفيش تاريخ متاح دلوقتي",
"dashboard":"📊 *لوحتك الشخصية*\n\n👤 الاسم: *{name}*\n🆔 ID: `{uid}`\n💎 الباقة: *{plan}*\n📅 ينتهي: *{expiry}*\n⏳ متبقي: *{days}* يوم\n⚡ فحص كل: *{interval}*\n\n━━━━━━━━━━━━━━━━━━\n📦 المنتجات: *{prods}/{plimit}*\n🌐 المواقع: *{sites}/{slimit}*\n💱 العملة: *{cur}*\n🕐 آخر فحص: *{last}*",
"daily_rep":"📊 *تقريرك اليومي*\n\n{items}\n⏰ {time}",
"weekly_rep":"📊 *تقريرك الأسبوعي*\n\n{items}\n⏰ {time}",
"rep_item":"📦 *{name}*: *{price}*\n",
"contact_prompt":"💬 اكتب رسالتك للأدمن:",
"contact_sent":"✅ تم إرسال رسالتك! هيرد عليك قريباً 🙏",
"enter_code":"🎁 أرسل كود التفعيل:",
"code_ok":"🎉 *كود صحيح!*\n\n💎 تم تفعيل باقة *{plan}* لمدة *{months}* شهر!\n📅 ينتهي: *{date}*",
"code_bad":"❌ الكود غلط أو منتهي الصلاحية!",
"help":"❓ *دليل الاستخدام:*\n\n➕ *إضافة منتج:* رابط ← Selector ← اسم ← تنبيه\n\n🌐 *مراقبة موقع (Pro+):* رابط صفحة عروض\n\n📈 *تاريخ الأسعار (Regular+)*\n\n🎁 */code* — تفعيل كود هدية\n\n💱 */currency* — تغيير العملة\n\n━━━━━━━━━━━━━━━━━━\n🆓 Free: 24h | 🔵 Regular: 12h\n🟢 Pro: 6h | 🟣 Premium: 3h | ⭐ Ultra: 30min",
"choose_cur":"💱 اختار العملة:",
"cur_changed":"✅ تم تغيير العملة إلى *{cur}*",
"lang_changed":"✅ تم تغيير اللغة 🇪🇬",
},
"en":{
"welcome":"🎉 *Welcome {name}!*\n\nSmart Price Tracker Bot 🛒\n\n━━━━━━━━━━━━━━━━━━\n🆓 *Free:* 3 products, daily check\n🔵 *Regular:* 10 products, every 12h\n🟢 *Pro:* 20 products, every 6h + report\n🟣 *Premium:* 30 products, every 3h\n⭐ *Ultra:* 50 products, every 30min\n\n━━━━━━━━━━━━━━━━━━\nTap any button to start 👇",
"sub_menu":"💎 *Subscribe*\n\n🆓 Free: 3 products - 24h check\n🔵 Regular: 10 products - 12h - 60EGP/mo\n🟢 Pro: 20 products - 6h - report - 120EGP/mo\n🟣 Premium: 30 products - 3h - reports - 200EGP/mo\n⭐ Ultra: 50 products - 30min - all features - 400EGP/mo\n\nChoose your plan 👇",
"sub_active":"💎 *Active Subscription:*\n\n📦 Plan: *{plan}*\n📅 Expires: *{date}*\n⏳ Days left: *{days}*\n⚡ Check every: *{interval}*\n📦 Products: *{prods}/{limit}*",
"choose_dur":"Choose duration:",
"choose_pay":"💳 Choose payment method:",
"pay_info":"📋 *Payment Details*\n\n📦 Plan: *{plan}*\n⏱ Duration: *{months}*\n💰 Amount: *{price} EGP*\n💳 Method: *{method}*\n\n━━━━━━━━━━━━━━━━━━\n1️⃣ Transfer to:\n`{number}`\n\n2️⃣ Send receipt screenshot 📸\n\n3️⃣ Wait for admin approval ✅\n\n⏰ Activation within 1 hour",
"receipt_ok":"✅ *Request received!*\n\nWill be activated within 1 hour ⏰\nThank you! 🙏",
"approved":"🎉 *Subscription Activated!*\n\n📦 Plan: *{plan}*\n📅 Expires: *{date}*\n⚡ Check every: *{interval}*\n\nEnjoy! 🚀",
"rejected":"❌ *Request Rejected*\n\nReason: {reason}\n\nContact admin 💬",
"invoice":"🧾 *Your Invoice*\n\n📦 Plan: *{plan}*\n⏱ Duration: *{months}* months\n💰 Amount: *{price} EGP*\n💳 Method: *{method}*\n📅 Expires: *{expiry}*\n🕐 {time}",
"expiry_warn":"⚠️ *Warning!*\n\nSubscription expires in *{days}* days!\n📅 Date: *{date}*\n\nRenew now 💎",
"expired":"⚠️ Subscription expired!\n\nDowngraded to Free 🆓\nRenew to keep all features 💎",
"send_url":"🔗 Send product URL:",
"send_sel":"🎯 Send CSS Selector:\n\nExample: `span.price`",
"send_name":"✏️ Enter product name:",
"choose_alert":"🔔 Choose alert type:",
"send_pct":"📉 Enter discount %:\nExample: `30` = when drops 30%+",
"send_tgt":"🎯 Enter target price:\nExample: `100` = when reaches 100 or less",
"checking":"⏳ Checking price...",
"added":"✅ *Added!*\n\n📦 *{name}*\n💰 Price: *{price}*\n🔔 Alert: *{alert}*\n🕐 {time}",
"al_any":"Any change","al_pct":"Drop {v}%+","al_tgt":"Reach {v} or less",
"err_url":"❌ Invalid URL!",
"err_price":"❌ Couldn't fetch price!",
"err_num":"❌ Enter a valid number!",
"limit":"⚠️ *Limit reached!*\n\nYour plan ({plan}) allows {limit} products only.\n\n💎 Upgrade for more!",
"no_prods":"📋 No products yet.\n\nPress ➕ to add!",
"checking_all":"⏳ Checking prices...",
"no_change":"✅ No changes\n⏰ {time}",
"price_down":"📉 *Price Dropped!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n💰 Discount: *{pct}%* 🎉\n\n🔗 {url}",
"price_up":"📈 *Price Increased!*\n\n📦 *{name}*\n❌ Was: *{old}*\n✅ Now: *{new}*\n\n🔗 {url}",
"price_tgt":"🎯 *Target Reached!*\n\n📦 *{name}*\n✅ Price: *{new}*\n\n🔗 {url}",
"deleted":"✅ Deleted",
"sel_delete":"🗑️ Choose to delete:",
"cancel":"❌ Cancelled",
"site_need_plan":"⚠️ Site monitoring requires Pro plan or higher",
"site_limit":"⚠️ Your plan allows {limit} sites only",
"send_surl":"🌐 Send deals page URL:",
"send_ssel":"🎯 Send price Selector:",
"send_sname":"✏️ Enter site name:",
"site_added":"✅ *Site Added!*\n🌐 *{name}*\n📦 Products: *{count}*",
"site_deals":"🔥 *New deals on {name}!*\n\n{deals}\n\n🔗 {url}",
"hist_need":"📈 Price history requires Regular plan or higher",
"hist_title":"📈 *Price history for {name}:*\n\n",
"hist_item":"📅 {date}: *{price}*\n",
"no_hist":"📈 No history available",
"dashboard":"📊 *Your Dashboard*\n\n👤 Name: *{name}*\n🆔 ID: `{uid}`\n💎 Plan: *{plan}*\n📅 Expires: *{expiry}*\n⏳ Days left: *{days}*\n⚡ Check every: *{interval}*\n\n━━━━━━━━━━━━━━━━━━\n📦 Products: *{prods}/{plimit}*\n🌐 Sites: *{sites}/{slimit}*\n💱 Currency: *{cur}*\n🕐 Last check: *{last}*",
"daily_rep":"📊 *Daily Report*\n\n{items}\n⏰ {time}",
"weekly_rep":"📊 *Weekly Report*\n\n{items}\n⏰ {time}",
"rep_item":"📦 *{name}*: *{price}*\n",
"contact_prompt":"💬 Write your message to admin:",
"contact_sent":"✅ Message sent! Admin will reply soon 🙏",
"enter_code":"🎁 Send your activation code:",
"code_ok":"🎉 *Valid Code!*\n\n💎 *{plan}* activated for *{months}* month(s)!\n📅 Expires: *{date}*",
"code_bad":"❌ Invalid or expired code!",
"help":"❓ *Usage Guide:*\n\n➕ *Add Product:* URL ← Selector ← Name ← Alert\n\n🌐 *Watch Site (Pro+):* Deals page URL\n\n📈 *Price History (Regular+)*\n\n🎁 */code* — Activate gift code\n\n💱 */currency* — Change currency",
"choose_cur":"💱 Choose currency:",
"cur_changed":"✅ Currency changed to *{cur}*",
"lang_changed":"✅ Language changed 🇬🇧",
}}

CURRENCIES={"EGP":"جنيه مصري","USD":"دولار","AED":"درهم إماراتي","SAR":"ريال سعودي"}

async def cmd_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; name=update.effective_user.first_name or "صديقي"
    data=load(); cid=str(uid)
    if cid not in data:
        data[cid]={"name":name,"joined":now_str(),"lang":"ar","currency":"EGP"}; save(data)
    if data.get(cid,{}).get("lang"):
        await update.message.reply_text(t(uid,"welcome",name=name),parse_mode="Markdown",reply_markup=main_kb(uid))
    else:
        await update.message.reply_text(f"👋 أهلاً *{name}*!\nاختار لغتك / Choose language:",parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇪🇬 عربي",callback_data="lang_ar"),InlineKeyboardButton("🇬🇧 English",callback_data="lang_en")]]))

async def cb_lang(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id
    lang=q.data.replace("lang_",""); udata=uget(uid); udata["lang"]=lang; usave(uid,udata)
    name=q.from_user.first_name or "صديقي"
    await q.edit_message_text("✅ Done!")
    await ctx.bot.send_message(uid,TX[lang]["welcome"].format(name=name),parse_mode="Markdown",reply_markup=main_kb(uid))

async def cmd_setlang(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("اختار اللغة / Choose language:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇪🇬 عربي",callback_data="lang_ar"),InlineKeyboardButton("🇬🇧 English",callback_data="lang_en")]]))

async def cmd_currency(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id
    await update.message.reply_text(t(uid,"choose_cur"),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(v,callback_data=f"cur_{k}")] for k,v in CURRENCIES.items()]))

async def cb_currency(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id
    cur=q.data.replace("cur_",""); udata=uget(uid); udata["currency"]=cur; usave(uid,udata)
    await q.edit_message_text(t(uid,"cur_changed",cur=CURRENCIES[cur]),parse_mode="Markdown")

async def cmd_setcommands(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return
    cmds=[BotCommand("start","🏠 القائمة الرئيسية"),BotCommand("add","➕ إضافة منتج"),
          BotCommand("list","📋 منتجاتي"),BotCommand("check","🔍 فحص الأسعار"),
          BotCommand("history","📈 تاريخ الأسعار"),BotCommand("dashboard","📊 لوحتي"),
          BotCommand("subscribe","💎 الاشتراك"),BotCommand("code","🎁 كود هدية"),
          BotCommand("currency","💱 العملة"),BotCommand("contact","💬 تواصل"),
          BotCommand("help","❓ مساعدة"),BotCommand("cancel","❌ إلغاء"),]
    await ctx.bot.set_my_commands(cmds)
    await update.message.reply_text("✅ تم تحديث الأوامر!")

async def cmd_subscribe(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; plan=get_plan(uid)
    if plan!="free":
        udata=uget(uid); sub=udata.get("subscription",{})
        expiry=datetime.fromisoformat(sub["expiry"]); days=(expiry-datetime.now()).days
        await update.message.reply_text(t(uid,"sub_active",plan=plan_lbl(plan),date=expiry.strftime("%Y-%m-%d"),
            days=days,interval=interval_lbl(plan,get_lang(uid)),prods=len(udata.get("products",{})),limit=PRODUCT_LIMITS[plan]),
            parse_mode="Markdown",reply_markup=main_kb(uid)); return
    kb=[[InlineKeyboardButton(PLANS[p]["name"],callback_data=f"sp_{p}")] for p in PLANS]
    await update.message.reply_text(t(uid,"sub_menu"),parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))

async def cb_sp(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id
    plan=q.data.replace("sp_",""); ctx.user_data["pay_plan"]=plan
    kb=[[InlineKeyboardButton(f"📅 {PLANS[plan]['labels'][d]}",callback_data=f"sd_{d}")] for d in ["1","3","12"]]
    await q.edit_message_text(t(uid,"choose_dur"),reply_markup=InlineKeyboardMarkup(kb))

async def cb_sd(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id
    ctx.user_data["pay_dur"]=q.data.replace("sd_","")
    kb=[[InlineKeyboardButton(v["name"],callback_data=f"sm_{k}")] for k,v in PAYMENT_METHODS.items()]
    await q.edit_message_text(t(uid,"choose_pay"),reply_markup=InlineKeyboardMarkup(kb))

async def cb_sm(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id
    mk=q.data.replace("sm_",""); plan=ctx.user_data.get("pay_plan","regular"); dur=ctx.user_data.get("pay_dur","1")
    price=PLANS[plan]["prices"][dur]; method=PAYMENT_METHODS[mk]
    ml={"1":"شهر","3":"3 شهور","12":"سنة"}[dur]
    await q.edit_message_text(t(uid,"pay_info",plan=PLANS[plan]["name"],months=ml,price=price,method=method["name"],number=method["number"]),parse_mode="Markdown")
    ctx.user_data.update({"await_receipt":True,"rplan":plan,"rdur":dur,"rmethod":method["name"],"rprice":price})

async def handle_receipt(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id
    if not ctx.user_data.get("await_receipt"): return False
    plan=ctx.user_data["rplan"]; dur=ctx.user_data["rdur"]
    method=ctx.user_data["rmethod"]; price=ctx.user_data["rprice"]
    name=update.effective_user.first_name or str(uid)
    req_id=f"{uid}_{int(datetime.now().timestamp())}"
    data=load()
    if "pending" not in data: data["pending"]={}
    data["pending"][req_id]={"uid":uid,"name":name,"plan":plan,"months":int(dur),"price":price,"method":method,"time":now_str(),"status":"pending"}
    save(data)
    if ADMIN_ID:
        cap=f"💳 *طلب دفع جديد!*\n\n👤 *{name}*\n🆔 `{uid}`\n📦 {PLANS[plan]['name']}\n⏱ {dur} شهر\n💰 {price}ج\n💳 {method}\n🕐 {now_str()}"
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة",callback_data=f"appr_{req_id}"),InlineKeyboardButton("❌ رفض",callback_data=f"rejt_{req_id}")]])
        try:
            if update.message.photo: await ctx.bot.send_photo(ADMIN_ID,update.message.photo[-1].file_id,caption=cap,parse_mode="Markdown",reply_markup=kb)
            else: await ctx.bot.send_message(ADMIN_ID,cap,parse_mode="Markdown",reply_markup=kb)
        except Exception as e: logger.error(f"admin notify: {e}")
    ctx.user_data["await_receipt"]=False
    await update.message.reply_text(t(uid,"receipt_ok"),parse_mode="Markdown",reply_markup=main_kb(uid))
    return True

async def cb_appr(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer("❌",show_alert=True); return
    req_id=q.data.replace("appr_",""); data=load()
    req=data.get("pending",{}).get(req_id)
    if not req: await q.answer("مش موجود!"); return
    uid=req["uid"]; plan=req["plan"]; months=req["months"]; price=req["price"]
    expiry=activate_sub(uid,plan,months)
    data["pending"][req_id]["status"]="approved"
    if "revenue" not in data: data["revenue"]=[]
    data["revenue"].append({"uid":uid,"name":req["name"],"plan":plan,"months":months,"price":price,"method":req["method"],"time":now_str()})
    save(data)
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text(f"✅ تم تفعيل {req['name']} (`{uid}`) — {plan_lbl(plan)} حتى {expiry}",parse_mode="Markdown")
    lang=get_lang(uid)
    try:
        await ctx.bot.send_message(uid,t(uid,"approved",plan=plan_lbl(plan,lang),date=expiry,interval=interval_lbl(plan,lang)),parse_mode="Markdown",reply_markup=main_kb(uid))
        await ctx.bot.send_message(uid,t(uid,"invoice",plan=plan_lbl(plan,lang),months=months,price=price,method=req["method"],expiry=expiry,time=now_str()),parse_mode="Markdown")
    except Exception as e: logger.error(f"notify approved: {e}")

async def cb_rejt(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer("❌",show_alert=True); return
    req_id=q.data.replace("rejt_",""); data=load()
    req=data.get("pending",{}).get(req_id)
    if not req: await q.answer("مش موجود!"); return
    uid=req["uid"]; data["pending"][req_id]["status"]="rejected"; save(data)
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text(f"❌ تم رفض طلب {req['name']} (`{uid}`)",parse_mode="Markdown")
    try: await ctx.bot.send_message(uid,t(uid,"rejected",reason="يرجى التواصل مع الأدمن"),parse_mode="Markdown",reply_markup=main_kb(uid))
    except: pass

async def cmd_code(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_chat.id,"enter_code")); return S_CODE

async def proc_code(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; code=update.message.text.strip().upper()
    data=load(); codes=data.get("activation_codes",{})
    if code not in codes or codes[code].get("used"):
        await update.message.reply_text(t(uid,"code_bad"),reply_markup=main_kb(uid)); return ConversationHandler.END
    c=codes[code]; expiry=activate_sub(uid,c["plan"],c["months"])
    data["activation_codes"][code].update({"used":True,"used_by":uid,"used_at":now_str()}); save(data)
    lang=get_lang(uid)
    await update.message.reply_text(t(uid,"code_ok",plan=plan_lbl(c["plan"],lang),months=c["months"],date=expiry),parse_mode="Markdown",reply_markup=main_kb(uid))
    return ConversationHandler.END

async def add_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; plan=get_plan(uid)
    prods=uget(uid).get("products",{}); lim=PRODUCT_LIMITS[plan]
    if len(prods)>=lim:
        await update.message.reply_text(t(uid,"limit",plan=plan_lbl(plan,get_lang(uid)),limit=lim),parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 ترقية",callback_data="open_sub")]])); return ConversationHandler.END
    await update.message.reply_text(t(uid,"send_url")); return S_URL

async def add_url(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; url=update.message.text.strip()
    if not url.startswith("http"): await update.message.reply_text(t(uid,"err_url")); return S_URL
    ctx.user_data["url"]=url; await update.message.reply_text(t(uid,"send_sel"),parse_mode="Markdown"); return S_SEL

async def add_sel(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; ctx.user_data["sel"]=update.message.text.strip()
    await update.message.reply_text(t(uid,"send_name")); return S_NAME

async def add_name(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; ctx.user_data["pname"]=update.message.text.strip()
    plan=get_plan(uid); feats=PLAN_FEATURES[plan]; lang=get_lang(uid)
    kb=[[InlineKeyboardButton("🔔 أي تغيير" if lang=="ar" else "🔔 Any Change",callback_data="al_any")]]
    if "percent" in feats["alerts"]:
        kb.append([InlineKeyboardButton("📉 نسبة خصم" if lang=="ar" else "📉 % Discount",callback_data="al_pct")])
    else:
        kb.append([InlineKeyboardButton("🔒 نسبة خصم (Regular+)",callback_data="al_lock")])
    if "target" in feats["alerts"]:
        kb.append([InlineKeyboardButton("🎯 سعر معين" if lang=="ar" else "🎯 Target Price",callback_data="al_tgt")])
    else:
        kb.append([InlineKeyboardButton("🔒 سعر معين (Regular+)",callback_data="al_lock")])
    await update.message.reply_text(t(uid,"choose_alert"),reply_markup=InlineKeyboardMarkup(kb)); return S_ALERT

async def cb_alert(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id
    if q.data=="al_lock": await q.answer("💎 يتطلب Regular أو أعلى",show_alert=True); return S_ALERT
    ctx.user_data["alert"]=q.data.replace("al_","")
    if ctx.user_data["alert"]=="any":
        await q.edit_message_text("✅ "+t(uid,"al_any")); await _do_add(ctx,uid); return ConversationHandler.END
    elif ctx.user_data["alert"]=="pct": await q.edit_message_text(t(uid,"send_pct"),parse_mode="Markdown")
    else: await q.edit_message_text(t(uid,"send_tgt"),parse_mode="Markdown")
    return S_AVAL

async def add_aval(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id
    try: float(update.message.text.strip())
    except: await update.message.reply_text(t(uid,"err_num")); return S_AVAL
    ctx.user_data["aval"]=update.message.text.strip(); await _do_add(ctx,uid,update); return ConversationHandler.END

async def _do_add(ctx,uid,update=None):
    url=ctx.user_data["url"]; sel=ctx.user_data["sel"]; pname=ctx.user_data["pname"]
    alert=ctx.user_data.get("alert","any"); aval=ctx.user_data.get("aval"); lang=get_lang(uid); now=now_str()
    msg=await ctx.bot.send_message(uid,t(uid,"checking"))
    price=await fetch_price(url,sel); await msg.delete()
    if not price: await ctx.bot.send_message(uid,t(uid,"err_price"),parse_mode="Markdown",reply_markup=main_kb(uid)); return
    adesc=t(uid,"al_any") if alert=="any" else t(uid,"al_pct",v=aval) if alert=="pct" else t(uid,"al_tgt",v=aval)
    udata=uget(uid)
    if "products" not in udata: udata["products"]={}
    pid=str(len(udata["products"])+1)
    udata["products"][pid]={"url":url,"sel":sel,"price":price,"name":pname,"added":now,"alert":alert,"aval":aval,"history":[{"price":price,"date":now}]}
    usave(uid,udata)
    await ctx.bot.send_message(uid,t(uid,"added",name=pname,price=price,alert=adesc,time=now),parse_mode="Markdown",reply_markup=main_kb(uid))

async def conv_cancel(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; await update.message.reply_text(t(uid,"cancel"),reply_markup=main_kb(uid)); return ConversationHandler.END

async def site_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; plan=get_plan(uid); slimit=SITE_LIMITS[plan]
    if slimit==0:
        await update.message.reply_text(t(uid,"site_need_plan"),parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 ترقية",callback_data="open_sub")]])); return ConversationHandler.END
    if len(uget(uid).get("sites",{}))>=slimit:
        await update.message.reply_text(t(uid,"site_limit",limit=slimit),parse_mode="Markdown"); return ConversationHandler.END
    await update.message.reply_text(t(uid,"send_surl"),parse_mode="Markdown"); return S_SURL

async def site_url(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; url=update.message.text.strip()
    if not url.startswith("http"): await update.message.reply_text(t(uid,"err_url")); return S_SURL
    ctx.user_data["surl"]=url; await update.message.reply_text(t(uid,"send_ssel"),parse_mode="Markdown"); return S_SSEL

async def site_sel(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; ctx.user_data["ssel"]=update.message.text.strip()
    await update.message.reply_text(t(uid,"send_sname")); return S_SNAME

async def site_name(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; sname=update.message.text.strip()
    prices=await fetch_all(ctx.user_data["surl"],ctx.user_data["ssel"])
    udata=uget(uid)
    if "sites" not in udata: udata["sites"]={}
    udata["sites"][str(len(udata["sites"])+1)]={"url":ctx.user_data["surl"],"sel":ctx.user_data["ssel"],"name":sname,"last_prices":prices,"added":now_str()}
    usave(uid,udata)
    await update.message.reply_text(t(uid,"site_added",name=sname,count=len(prices)),parse_mode="Markdown",reply_markup=main_kb(uid))
    return ConversationHandler.END

async def cmd_list(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; plan=get_plan(uid); udata=uget(uid)
    prods=udata.get("products",{}); sites=udata.get("sites",{})
    if not prods and not sites: await update.message.reply_text(t(uid,"no_prods"),reply_markup=main_kb(uid)); return
    text=""
    if prods:
        text+=f"📋 *منتجاتي ({len(prods)}/{PRODUCT_LIMITS[plan]}):*\n\n"
        for i,(pid,p) in enumerate(prods.items(),1):
            icon={"any":"🔔","pct":"📉","tgt":"🎯"}.get(p.get("alert","any"),"🔔")
            text+=f"*{i}.* 📦 {p['name']}\n   💰 *{p['price']}* {icon}\n   🔗 {p['url'][:35]}...\n\n"
    if sites:
        text+=f"🌐 *المواقع ({len(sites)}):*\n\n"
        for i,(sid,s) in enumerate(sites.items(),1):
            text+=f"*{i}.* 🌐 {s['name']}\n   🔗 {s['url'][:35]}...\n\n"
    await update.message.reply_text(text,parse_mode="Markdown",reply_markup=main_kb(uid))

async def cmd_check(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; udata=uget(uid); prods=udata.get("products",{})
    if not prods: await update.message.reply_text(t(uid,"no_prods"),reply_markup=main_kb(uid)); return
    await update.message.reply_text(t(uid,"checking_all")); changed=0; now=now_str()
    for pid,p in prods.items():
        np=await fetch_price(p["url"],p["sel"])
        if not np or np==p["price"]: continue
        of,nf=p2f(p["price"]),p2f(np); alert=p.get("alert","any"); aval=p.get("aval")
        notify,mk=False,"price_down"
        if alert=="any": notify=True; mk="price_down" if (of and nf and nf<of) else "price_up"
        elif alert=="pct" and of and nf and nf<of:
            if ((of-nf)/of*100)>=float(aval or 0): notify=True
        elif alert=="tgt" and nf and nf<=float(aval or 0): notify=True; mk="price_tgt"
        if notify:
            udata["products"][pid]["price"]=np
            if "history" not in udata["products"][pid]: udata["products"][pid]["history"]=[]
            udata["products"][pid]["history"].append({"price":np,"date":now})
            udata["products"][pid]["history"]=udata["products"][pid]["history"][-30:]
            changed+=1; pct=round((of-nf)/of*100) if of and nf else 0
            await update.message.reply_text(t(uid,mk,name=p["name"],old=p["price"],new=np,pct=pct,url=p["url"]),parse_mode="Markdown")
    udata["last_check"]=now; usave(uid,udata)
    if changed==0: await update.message.reply_text(t(uid,"no_change",time=now),reply_markup=main_kb(uid))

async def cmd_history(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; plan=get_plan(uid)
    if not PLAN_FEATURES[plan]["history"]:
        await update.message.reply_text(t(uid,"hist_need"),parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 ترقية",callback_data="open_sub")]])); return
    prods=uget(uid).get("products",{})
    if not prods: await update.message.reply_text(t(uid,"no_prods"),reply_markup=main_kb(uid)); return
    kb=[[InlineKeyboardButton(f"📦 {p['name']}",callback_data=f"hist_{pid}")] for pid,p in prods.items()]
    await update.message.reply_text("📈 اختار المنتج:",reply_markup=InlineKeyboardMarkup(kb))

async def cb_hist(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id
    pid=q.data.replace("hist_",""); p=uget(uid).get("products",{}).get(pid)
    if not p: await q.edit_message_text("❌"); return
    hist=p.get("history",[])
    if not hist: await q.edit_message_text(t(uid,"no_hist"),parse_mode="Markdown"); return
    text=t(uid,"hist_title",name=p["name"])
    for h in hist[-15:]: text+=t(uid,"hist_item",date=h["date"],price=h["price"])
    await q.edit_message_text(text,parse_mode="Markdown")

async def cmd_delete(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; udata=uget(uid)
    prods=udata.get("products",{}); sites=udata.get("sites",{})
    if not prods and not sites: await update.message.reply_text(t(uid,"no_prods"),reply_markup=main_kb(uid)); return
    kb=([[InlineKeyboardButton(f"📦 {p['name']} — {p['price']}",callback_data=f"dp_{pid}")] for pid,p in prods.items()]+
        [[InlineKeyboardButton(f"🌐 {s['name']}",callback_data=f"ds_{sid}")] for sid,s in sites.items()])
    await update.message.reply_text(t(uid,"sel_delete"),reply_markup=InlineKeyboardMarkup(kb))

async def cb_del(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id; udata=uget(uid)
    if q.data.startswith("dp_"): udata.get("products",{}).pop(q.data.replace("dp_",""),None)
    elif q.data.startswith("ds_"): udata.get("sites",{}).pop(q.data.replace("ds_",""),None)
    usave(uid,udata); await q.edit_message_text(t(uid,"deleted"))
    await ctx.bot.send_message(uid,"🏠",reply_markup=main_kb(uid))

async def cmd_dashboard(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; plan=get_plan(uid); udata=uget(uid); lang=get_lang(uid)
    sub=udata.get("subscription",{}); name=update.effective_user.first_name or str(uid)
    if sub and plan!="free":
        exp=datetime.fromisoformat(sub["expiry"]); expiry=exp.strftime("%Y-%m-%d"); days=(exp-datetime.now()).days
    else: expiry="—"; days=0
    await update.message.reply_text(t(uid,"dashboard",name=name,uid=uid,plan=plan_lbl(plan,lang),
        expiry=expiry,days=days,interval=interval_lbl(plan,lang),
        prods=len(udata.get("products",{})),plimit=PRODUCT_LIMITS[plan],
        sites=len(udata.get("sites",{})),slimit=SITE_LIMITS[plan],
        cur=get_cur(uid),last=udata.get("last_check","—")),parse_mode="Markdown",reply_markup=main_kb(uid))

async def contact_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; await update.message.reply_text(t(uid,"contact_prompt")); return S_CONTACT

async def contact_send(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; plan=get_plan(uid); name=update.effective_user.first_name or str(uid); lang=get_lang(uid)
    if ADMIN_ID:
        prio="⭐ ULTRA — أولوية قصوى!\n" if plan=="ultra" else ""
        await ctx.bot.send_message(ADMIN_ID,f"💬 *رسالة*\n{prio}\n👤 {name}\n🆔 `{uid}`\n💎 {plan_lbl(plan,lang)}\n\n📝 {update.message.text}",parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ رد",callback_data=f"reply_{uid}")]]))
    await update.message.reply_text(t(uid,"contact_sent"),reply_markup=main_kb(uid)); return ConversationHandler.END

async def cmd_admin(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id
    if uid!=ADMIN_ID: return
    data=load(); users=[k for k in data if k.isdigit()]
    prem=[k for k in users if get_plan(int(k))!="free"]
    pend=[v for v in data.get("pending",{}).values() if v["status"]=="pending"]
    rev=sum(v["price"] for v in data.get("revenue",[]))
    pc=defaultdict(int)
    for k in users: pc[get_plan(int(k))]+=1
    text=(f"👑 *لوحة الأدمن*\n\n👥 المستخدمين: *{len(users)}*\n💎 المشتركين: *{len(prem)}*\n"
          f"🆓 المجانيين: *{len(users)-len(prem)}*\n⏳ طلبات معلقة: *{len(pend)}*\n💰 الإيرادات: *{rev} جنيه*\n\n"
          f"📊 الباقات:\n"+"\n".join([f"  {plan_lbl(p,'ar')}: {c}" for p,c in pc.items()]))
    await update.message.reply_text(text,parse_mode="Markdown",reply_markup=admin_kb())

async def admin_users(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return
    data=load(); users=[(k,v) for k,v in data.items() if k.isdigit()]
    if not users: await update.message.reply_text("مفيش مستخدمين"); return
    text="👥 *المستخدمين:*\n\n"
    for uid,ud in users[:30]:
        plan=get_plan(int(uid)); icon={"free":"🆓","regular":"🔵","pro":"🟢","premium":"🟣","ultra":"⭐"}.get(plan,"🆓")
        text+=f"{icon} *{ud.get('name','—')}* (`{uid}`)\n   📦 {len(ud.get('products',{}))}\n\n"
    await update.message.reply_text(text,parse_mode="Markdown",reply_markup=admin_kb())

async def admin_premium(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return
    data=load(); prem=[(k,v) for k,v in data.items() if k.isdigit() and get_plan(int(k))!="free"]
    if not prem: await update.message.reply_text("مفيش مشتركين"); return
    text="💎 *المشتركين:*\n\n"
    for uid,ud in prem:
        plan=get_plan(int(uid)); sub=ud.get("subscription",{})
        exp=datetime.fromisoformat(sub["expiry"]); days=(exp-datetime.now()).days
        text+=f"{plan_lbl(plan,'ar')} *{ud.get('name','—')}* (`{uid}`)\n   📅 {exp.strftime('%Y-%m-%d')} ({days}d)\n\n"
    await update.message.reply_text(text,parse_mode="Markdown",reply_markup=admin_kb())

async def admin_pending(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return
    data=load(); pend=[(k,v) for k,v in data.get("pending",{}).items() if v["status"]=="pending"]
    if not pend: await update.message.reply_text("✅ مفيش طلبات معلقة"); return
    for rid,req in pend:
        await update.message.reply_text(
            f"💳 *طلب دفع*\n👤 {req['name']} (`{req['uid']}`)\n📦 {req['plan']}\n💰 {req['price']}ج\n💳 {req['method']}\n🕐 {req['time']}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ موافقة",callback_data=f"appr_{rid}"),InlineKeyboardButton("❌ رفض",callback_data=f"rejt_{rid}")]]))

async def admin_revenue(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return
    data=load(); rev=data.get("revenue",[])
    if not rev: await update.message.reply_text("💰 مفيش إيرادات بعد"); return
    total=sum(r["price"] for r in rev)
    today=datetime.now().strftime("%Y-%m-%d"); month=datetime.now().strftime("%Y-%m")
    td=sum(r["price"] for r in rev if r["time"].startswith(today))
    mo=sum(r["price"] for r in rev if r["time"].startswith(month))
    text=(f"💰 *الإيرادات:*\n\n📅 اليوم: *{td} جنيه*\n📆 الشهر: *{mo} جنيه*\n💎 الإجمالي: *{total} جنيه*\n\nآخر 10 عمليات:\n")
    for r in rev[-10:][::-1]:
        text+=f"• {r['name']} — {r['price']}ج — {r['plan']} — {r['time'][:10]}\n"
    await update.message.reply_text(text,parse_mode="Markdown",reply_markup=admin_kb())

async def admin_codes(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return
    data=load(); codes=data.get("activation_codes",{})
    unused={k:v for k,v in codes.items() if not v.get("used")}
    used={k:v for k,v in codes.items() if v.get("used")}
    text=f"🎁 *الأكواد:*\n\n✅ غير مستخدمة: *{len(unused)}*\n❌ مستخدمة: *{len(used)}*\n\n"
    for code,c in list(unused.items())[:10]:
        text+=f"`{code}` — {plan_lbl(c['plan'],'ar')} — {c['months']}شهر\n"
    await update.message.reply_text(text,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ إنشاء كود",callback_data="gen_code")]]))

async def cb_gen(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: await q.answer("❌"); return
    await q.answer()
    kb=[[InlineKeyboardButton(PLANS[p]["name"],callback_data=f"nc_p_{p}")] for p in PLANS]
    await q.edit_message_text("اختار الباقة:",reply_markup=InlineKeyboardMarkup(kb))

async def cb_nc_plan(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); ctx.user_data["nc_plan"]=q.data.replace("nc_p_","")
    kb=[[InlineKeyboardButton("1 شهر",callback_data="nc_d_1"),InlineKeyboardButton("3 شهور",callback_data="nc_d_3"),InlineKeyboardButton("سنة",callback_data="nc_d_12")]]
    await q.edit_message_text("اختار المدة:",reply_markup=InlineKeyboardMarkup(kb))

async def cb_nc_dur(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    months=int(q.data.replace("nc_d_","")); plan=ctx.user_data.get("nc_plan","regular")
    code=gen_code(); data=load()
    if "activation_codes" not in data: data["activation_codes"]={}
    data["activation_codes"][code]={"plan":plan,"months":months,"used":False,"created":now_str()}
    save(data)
    await q.edit_message_text(f"🎁 *كود جديد!*\n\n🔑 الكود: `{code}`\n📦 الباقة: *{plan_lbl(plan,'ar')}*\n⏱ المدة: *{months} شهر*",parse_mode="Markdown")

async def asub_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text("أرسل ID المستخدم:"); return S_ASUB_ID

async def asub_id(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    try: ctx.user_data["asub_uid"]=int(update.message.text.strip())
    except: await update.message.reply_text("❌ ID غلط!"); return S_ASUB_ID
    kb=[[InlineKeyboardButton(PLANS[p]["name"],callback_data=f"asp_{p}")] for p in PLANS]
    await update.message.reply_text("اختار الباقة:",reply_markup=InlineKeyboardMarkup(kb)); return S_ASUB_PLAN

async def cb_asp(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    ctx.user_data["asub_plan"]=q.data.replace("asp_",""); await q.edit_message_text("أرسل عدد الأشهر:"); return S_ASUB_MON

async def asub_mon(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    try:
        months=int(update.message.text.strip()); uid=ctx.user_data["asub_uid"]; plan=ctx.user_data["asub_plan"]
        expiry=activate_sub(uid,plan,months)
        await update.message.reply_text(f"✅ تم تفعيل {plan_lbl(plan,'ar')} للمستخدم `{uid}` لمدة {months} شهر حتى {expiry}",parse_mode="Markdown",reply_markup=admin_kb())
        try: await ctx.bot.send_message(uid,t(uid,"approved",plan=plan_lbl(plan,get_lang(uid)),date=expiry,interval=interval_lbl(plan,get_lang(uid))),parse_mode="Markdown",reply_markup=main_kb(uid))
        except: pass
    except Exception as e: await update.message.reply_text(f"❌ {e}")
    return ConversationHandler.END

async def broadcast_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text("📢 اكتب الرسالة:"); return S_BROADCAST

async def broadcast_send(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id!=ADMIN_ID: return ConversationHandler.END
    data=load(); users=[k for k in data if k.isdigit()]; sent=0
    for uid in users:
        try: await ctx.bot.send_message(int(uid),update.message.text,parse_mode="Markdown"); sent+=1
        except: pass
    await update.message.reply_text(f"✅ تم الإرسال لـ {sent} مستخدم",reply_markup=admin_kb()); return ConversationHandler.END

async def _check_uid(ctx,uid_str,udata):
    uid=int(uid_str); prods=udata.get("products",{}); now=now_str(); changed=False
    for pid,p in prods.items():
        np=await fetch_price(p["url"],p["sel"])
        if not np or np==p["price"]: continue
        of,nf=p2f(p["price"]),p2f(np); alert=p.get("alert","any"); aval=p.get("aval")
        notify,mk=False,"price_down"
        if alert=="any": notify=True; mk="price_down" if (of and nf and nf<of) else "price_up"
        elif alert=="pct" and of and nf and nf<of:
            if ((of-nf)/of*100)>=float(aval or 0): notify=True
        elif alert=="tgt" and nf and nf<=float(aval or 0): notify=True; mk="price_tgt"
        if notify:
            udata["products"][pid]["price"]=np
            if "history" not in udata["products"][pid]: udata["products"][pid]["history"]=[]
            udata["products"][pid]["history"].append({"price":np,"date":now})
            udata["products"][pid]["history"]=udata["products"][pid]["history"][-30:]
            changed=True; pct=round((of-nf)/of*100) if of and nf else 0
            try: await ctx.bot.send_message(uid,t(uid,mk,name=p["name"],old=p["price"],new=np,pct=pct,url=p["url"]),parse_mode="Markdown")
            except Exception as e: logger.error(f"notify {uid}: {e}")
    for sid,s in udata.get("sites",{}).items():
        np2=await fetch_all(s["url"],s["sel"]); nd=set(np2)-set(s.get("last_prices",[]))
        if nd:
            udata["sites"][sid]["last_prices"]=np2; changed=True
            try: await ctx.bot.send_message(uid,t(uid,"site_deals",name=s["name"],deals="\n".join([f"💰 {p}" for p in list(nd)[:10]]),url=s["url"]),parse_mode="Markdown")
            except Exception as e: logger.error(f"site {uid}: {e}")
    if changed: udata["last_check"]=now; usave(uid,udata)

async def auto_check(ctx:ContextTypes.DEFAULT_TYPE):
    data=load(); now_dt=datetime.now()
    for uid_str,udata in data.items():
        if not uid_str.isdigit(): continue
        plan=get_plan(int(uid_str)); interval=CHECK_INTERVALS[plan]
        last=udata.get("last_check","2000-01-01 00:00")
        try: last_dt=datetime.strptime(last,"%Y-%m-%d %H:%M")
        except: last_dt=datetime.min
        if (now_dt-last_dt).total_seconds()>=interval:
            await _check_uid(ctx,uid_str,udata)

async def check_expiry(ctx:ContextTypes.DEFAULT_TYPE):
    data=load()
    for uid_str,udata in data.items():
        if not uid_str.isdigit(): continue
        sub=udata.get("subscription")
        if not sub: continue
        try:
            exp=datetime.fromisoformat(sub["expiry"]); days=(exp-datetime.now()).days
            if days in [3,1]: await ctx.bot.send_message(int(uid_str),t(int(uid_str),"expiry_warn",days=days,date=exp.strftime("%Y-%m-%d")),parse_mode="Markdown")
            if days<0 and sub.get("plan")!="free":
                udata["subscription"]["plan"]="free"; usave(int(uid_str),udata)
                await ctx.bot.send_message(int(uid_str),t(int(uid_str),"expired"),parse_mode="Markdown",reply_markup=main_kb(int(uid_str)))
        except Exception as e: logger.error(f"expiry {uid_str}: {e}")

async def send_reports(ctx:ContextTypes.DEFAULT_TYPE):
    data=load(); now=now_str()
    for uid_str,udata in data.items():
        if not uid_str.isdigit(): continue
        plan=get_plan(int(uid_str)); feats=PLAN_FEATURES[plan]; prods=udata.get("products",{})
        if not prods or not feats["daily"]: continue
        lang=get_lang(int(uid_str))
        items="".join([TX[lang]["rep_item"].format(name=p["name"],price=p["price"]) for p in prods.values()])
        try: await ctx.bot.send_message(int(uid_str),TX[lang]["daily_rep"].format(items=items,time=now),parse_mode="Markdown")
        except: pass

async def btn_handler(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_chat.id; text=update.message.text; lang=get_lang(uid)
    if uid==ADMIN_ID:
        if text=="👥 المستخدمين": await admin_users(update,ctx); return
        if text=="💎 المشتركين": await admin_premium(update,ctx); return
        if text=="📋 طلبات الدفع": await admin_pending(update,ctx); return
        if text=="📊 الإيرادات": await admin_revenue(update,ctx); return
        if text=="🎁 الأكواد": await admin_codes(update,ctx); return
        if text=="🏠 رجوع": await update.message.reply_text("🏠",reply_markup=main_kb(uid)); return
    btns_ar={"📋 منتجاتي":cmd_list,"🔍 فحص الآن":cmd_check,"📈 تاريخ الأسعار":cmd_history,
             "📊 لوحتي":cmd_dashboard,"💎 الاشتراك":cmd_subscribe,"🌐 اللغة":cmd_setlang,
             "💱 العملة":cmd_currency,"❓ مساعدة":lambda u,c:u.message.reply_text(t(u.effective_chat.id,"help"),parse_mode="Markdown",reply_markup=main_kb(u.effective_chat.id))}
    btns_en={"📋 My Products":cmd_list,"🔍 Check Now":cmd_check,"📈 Price History":cmd_history,
             "📊 Dashboard":cmd_dashboard,"💎 Subscribe":cmd_subscribe,"🌐 Language":cmd_setlang,
             "💱 Currency":cmd_currency,"❓ Help":lambda u,c:u.message.reply_text(t(u.effective_chat.id,"help"),parse_mode="Markdown",reply_markup=main_kb(u.effective_chat.id))}
    btns={**btns_ar,**btns_en}
    if text in btns: await btns[text](update,ctx)

async def photo_handler(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    await handle_receipt(update,ctx)

async def cb_open_sub(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.message.chat_id; lang=get_lang(uid)
    kb=[[InlineKeyboardButton(PLANS[p]["name"],callback_data=f"sp_{p}")] for p in PLANS]
    await ctx.bot.send_message(uid,TX[lang]["sub_menu"],parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))

def main():
    app=Application.builder().token(BOT_TOKEN).build()
    add_conv=ConversationHandler(
        entry_points=[CommandHandler("add",add_start),MessageHandler(filters.Regex(r"^(➕ إضافة منتج|➕ Add Product)$"),add_start)],
        states={S_URL:[MessageHandler(filters.TEXT&~filters.COMMAND,add_url)],S_SEL:[MessageHandler(filters.TEXT&~filters.COMMAND,add_sel)],
                S_NAME:[MessageHandler(filters.TEXT&~filters.COMMAND,add_name)],S_ALERT:[CallbackQueryHandler(cb_alert,pattern=r"^al_")],
                S_AVAL:[MessageHandler(filters.TEXT&~filters.COMMAND,add_aval)]},
        fallbacks=[CommandHandler("cancel",conv_cancel)])
    site_conv=ConversationHandler(
        entry_points=[CommandHandler("watch",site_start),MessageHandler(filters.Regex(r"^(🌐 مراقبة موقع|🌐 Watch Site)$"),site_start)],
        states={S_SURL:[MessageHandler(filters.TEXT&~filters.COMMAND,site_url)],S_SSEL:[MessageHandler(filters.TEXT&~filters.COMMAND,site_sel)],
                S_SNAME:[MessageHandler(filters.TEXT&~filters.COMMAND,site_name)]},
        fallbacks=[CommandHandler("cancel",conv_cancel)])
    contact_conv=ConversationHandler(
        entry_points=[CommandHandler("contact",contact_start),MessageHandler(filters.Regex(r"^(💬 تواصل|💬 Contact)$"),contact_start)],
        states={S_CONTACT:[MessageHandler(filters.TEXT&~filters.COMMAND,contact_send)]},
        fallbacks=[CommandHandler("cancel",conv_cancel)])
    code_conv=ConversationHandler(
        entry_points=[CommandHandler("code",cmd_code)],
        states={S_CODE:[MessageHandler(filters.TEXT&~filters.COMMAND,proc_code)]},
        fallbacks=[CommandHandler("cancel",conv_cancel)])
    asub_conv=ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ اشتراك يدوي$"),asub_start)],
        states={S_ASUB_ID:[MessageHandler(filters.TEXT&~filters.COMMAND,asub_id)],S_ASUB_PLAN:[CallbackQueryHandler(cb_asp,pattern=r"^asp_")],
                S_ASUB_MON:[MessageHandler(filters.TEXT&~filters.COMMAND,asub_mon)]},
        fallbacks=[CommandHandler("cancel",conv_cancel)])
    bc_conv=ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📢 رسالة للكل$"),broadcast_start)],
        states={S_BROADCAST:[MessageHandler(filters.TEXT&~filters.COMMAND,broadcast_send)]},
        fallbacks=[CommandHandler("cancel",conv_cancel)])
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("admin",cmd_admin))
    app.add_handler(CommandHandler("list",cmd_list))
    app.add_handler(CommandHandler("check",cmd_check))
    app.add_handler(CommandHandler("history",cmd_history))
    app.add_handler(CommandHandler("dashboard",cmd_dashboard))
    app.add_handler(CommandHandler("subscribe",cmd_subscribe))
    app.add_handler(CommandHandler("setcommands",cmd_setcommands))
    app.add_handler(CommandHandler("currency",cmd_currency))
    app.add_handler(CommandHandler("help",lambda u,c:u.message.reply_text(t(u.effective_chat.id,"help"),parse_mode="Markdown",reply_markup=main_kb(u.effective_chat.id))))
    app.add_handler(CallbackQueryHandler(cb_lang,pattern=r"^lang_"))
    app.add_handler(CallbackQueryHandler(cb_currency,pattern=r"^cur_"))
    app.add_handler(CallbackQueryHandler(cb_sp,pattern=r"^sp_"))
    app.add_handler(CallbackQueryHandler(cb_sd,pattern=r"^sd_"))
    app.add_handler(CallbackQueryHandler(cb_sm,pattern=r"^sm_"))
    app.add_handler(CallbackQueryHandler(cb_appr,pattern=r"^appr_"))
    app.add_handler(CallbackQueryHandler(cb_rejt,pattern=r"^rejt_"))
    app.add_handler(CallbackQueryHandler(cb_del,pattern=r"^d[ps]_"))
    app.add_handler(CallbackQueryHandler(cb_hist,pattern=r"^hist_"))
    app.add_handler(CallbackQueryHandler(cb_open_sub,pattern=r"^open_sub$"))
    app.add_handler(CallbackQueryHandler(cb_gen,pattern=r"^gen_code$"))
    app.add_handler(CallbackQueryHandler(cb_nc_plan,pattern=r"^nc_p_"))
    app.add_handler(CallbackQueryHandler(cb_nc_dur,pattern=r"^nc_d_"))
    app.add_handler(add_conv); app.add_handler(site_conv); app.add_handler(contact_conv)
    app.add_handler(code_conv); app.add_handler(asub_conv); app.add_handler(bc_conv)
    app.add_handler(MessageHandler(filters.PHOTO,photo_handler))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,btn_handler))
    app.job_queue.run_repeating(auto_check,interval=1800,first=60)
    app.job_queue.run_repeating(check_expiry,interval=3600,first=300)
    app.job_queue.run_daily(send_reports,time=datetime.strptime("09:00","%H:%M").time())
    logger.info("✅ البوت شغال!")
    app.run_polling()

if __name__=="__main__":
    main()
