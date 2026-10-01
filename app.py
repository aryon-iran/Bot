"""
ARYON Bot v2 — Official bot for Bale Messenger
Deploy target: Render Web Service
Includes: User registration, admin messaging, no pricing, premium UX
"""

# =========================
# IMPORTS
# =========================
import os
import re
import json
import time
import sqlite3
import logging
import asyncio
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Tuple
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse

# =========================
# CONFIG (pre-filled with provided credentials, ENV overrides)
# =========================
BALE_BOT_TOKEN: str = os.getenv(
    "BALE_BOT_TOKEN",
    "1097976151:cgPxiahbDONHRDNBjYpOQW1_vcKYTrrN3n0",
)
ADMIN_IDS_RAW: str = os.getenv("ADMIN_IDS", "1722420726")
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
PORT: int = int(os.getenv("PORT", "10000"))
SUPPORT_BOT_LINK: str = os.getenv("SUPPORT_BOT_LINK", "https://ble.ir/ARYON_Support")
DB_PATH: str = os.getenv("DB_PATH", "aryon.db")
BALE_API_BASE: str = "https://tapi.bale.ai/bot"

# Parse admin IDs
ADMIN_IDS: set = set()
if ADMIN_IDS_RAW:
    for _aid in ADMIN_IDS_RAW.split(","):
        _aid = _aid.strip()
        if _aid.isdigit():
            ADMIN_IDS.add(int(_aid))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("aryon-bot")

# Rate limit
_rate_limit_store: Dict[int, List[float]] = {}
RATE_LIMIT_WINDOW: int = 2
RATE_LIMIT_MAX: int = 6

# =========================
# DATABASE
# =========================
_db_lock = asyncio.Lock()


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


async def db_execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    async with _db_lock:
        conn = _db_conn()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur
        finally:
            conn.close()


async def db_fetchone(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    async with _db_lock:
        conn = _db_conn()
        try:
            cur = conn.execute(sql, params)
            return cur.fetchone()
        finally:
            conn.close()


async def db_fetchall(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    async with _db_lock:
        conn = _db_conn()
        try:
            cur = conn.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()


def init_db() -> None:
    """Create tables and seed defaults."""
    conn = _db_conn()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            phone TEXT DEFAULT '',
            is_registered INTEGER DEFAULT 0,
            join_date TEXT,
            last_activity TEXT,
            is_blocked INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            project_type TEXT,
            title TEXT,
            description TEXT,
            features TEXT,
            budget TEXT,
            deadline TEXT,
            contact TEXT,
            status TEXT DEFAULT 'submitted',
            admin_note TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            features TEXT,
            delivery_time TEXT,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            title TEXT,
            description TEXT,
            technologies TEXT,
            link TEXT,
            image_url TEXT
        );

        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            image_url TEXT,
            link TEXT,
            created_at TEXT,
            published INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS faq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            created_at TEXT,
            is_read INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    # Seed services (NO PRICING)
    if cur.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
        services = [
            ("🌐 طراحی سایت", "طراحی وبسایت مدرن، سریع و ریسپانسیو مطابق برند شما", "وردپرس، HTML/CSS/JS، React، Next.js", "۷ تا ۳۰ روز", 1),
            ("🤖 ساخت ربات", "ربات‌های تلگرام، بله و اتوماسیون پیام‌رسان", "Python، API، Database، Webhook", "۵ تا ۲۰ روز", 1),
            ("📱 اپلیکیشن", "اپلیکیشن موبایل اندروید و iOS با UI حرفه‌ای", "Flutter، React Native، Native", "۲۰ تا ۶۰ روز", 1),
            ("⚙️ اتوماسیون", "خودکارسازی فرایندهای کسب‌وکار و اتصال سرویس‌ها", "Python، Selenium، API، n8n", "۷ تا ۲۵ روز", 1),
            ("🎨 طراحی UI/UX", "طراحی رابط و تجربه کاربری حرفه‌ای و مدرن", "Figma، Adobe XD، Prototyping", "۵ تا ۱۵ روز", 1),
            ("💻 پروژه اختصاصی", "توسعه نرم‌افزار سفارشی مطابق نیاز دقیق شما", "Python، JavaScript، Go، Rust", "متغیر", 1),
            ("📦 محصولات آماده", "محصولات نرم‌افزاری آماده خرید و سفارشی‌سازی", "متنوع", "فوری", 1),
        ]
        cur.executemany(
            "INSERT INTO services (name, description, features, delivery_time, is_active) VALUES (?,?,?,?,?)",
            services,
        )

    # Seed FAQ
    if cur.execute("SELECT COUNT(*) FROM faq").fetchone()[0] == 0:
        faqs = [
            ("❓ هزینه ساخت سایت چقدر است؟", "هزینه بستگی به نوع سایت، امکانات و پیچیدگی آن دارد. برای دریافت مشاوره و برآورد دقیق، سفارش خود را ثبت کنید تا تیم ما با شما تماس بگیرد."),
            ("❓ ساخت ربات چقدر زمان می‌برد؟", "بسته به پیچیدگی، بین ۵ تا ۲۰ روز کاری. زمان دقیق پس از بررسی نیازمندی‌ها اعلام می‌شود."),
            ("❓ چطور سفارش ثبت کنم؟", "از منوی اصلی گزینه «ثبت سفارش» را انتخاب کنید و فرم را تکمیل کنید. تیم ما در اسرع وقت با شما تماس می‌گیرد."),
            ("❓ چطور سفارش را پیگیری کنم؟", "از منوی «پروژه‌های من» می‌توانید وضعیت سفارش خود را به‌صورت لحظه‌ای ببینید."),
            ("❓ آیا پروژه اختصاصی قبول می‌کنید؟", "بله، تیم ARYON آماده اجرای پروژه‌های اختصاصی مطابق نیاز شماست. کافیست سفارش ثبت کنید."),
            ("❓ چطور با پشتیبانی ارتباط بگیرم؟", "از دکمه «پشتیبانی» در منوی اصلی استفاده کنید یا سفارش خود را ثبت کنید."),
        ]
        cur.executemany("INSERT INTO faq (question, answer) VALUES (?,?)", faqs)

    # Seed settings
    if cur.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", ("support_link", SUPPORT_BOT_LINK))
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", ("about_text", (
            "ℹ️ درباره ARYON\n\n"
            "ARYON یک مجموعه فعال در حوزه توسعه و ساخت محصولات دیجیتال است.\n\n"
            "🌐 توسعه وب\n🤖 ربات\n📱 اپلیکیشن\n⚙️ اتوماسیون\n🎨 طراحی\n💻 پروژه اختصاصی\n\n"
            "ما با تیمی از متخصصان، ایده‌های شما را به واقعیت تبدیل می‌کنیم."
        )))

    conn.commit()
    conn.close()
    log.info("Database initialized.")


# =========================
# BALE API
# =========================
_http_client: Optional[httpx.AsyncClient] = None


async def get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def bale_api(method: str, payload: Dict[str, Any]) -> Optional[Dict]:
    if not BALE_BOT_TOKEN:
        log.error("BALE_BOT_TOKEN not set.")
        return None
    url = f"{BALE_API_BASE}{BALE_BOT_TOKEN}/{method}"
    client = await get_http()
    try:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            log.warning("Bale API %s returned ok=False: %s", method, data.get("description"))
        return data
    except httpx.HTTPStatusError as e:
        log.error("Bale API HTTP error %s: %s", method, e.response.text[:300])
    except Exception as e:
        log.error("Bale API error %s: %s", method, e)
    return None


async def send_message(chat_id: int, text: str, reply_markup: Optional[Dict] = None) -> Optional[Dict]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await bale_api("sendMessage", payload)


async def edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict] = None) -> Optional[Dict]:
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await bale_api("editMessageText", payload)


async def answer_callback(callback_query_id: str, text: str = "") -> None:
    await bale_api("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})


async def set_webhook(url: str) -> None:
    result = await bale_api("setWebhook", {"url": url})
    if result and result.get("ok"):
        log.info("Webhook set to %s", url)
    else:
        log.error("Failed to set webhook: %s", result)


# =========================
# KEYBOARDS
# =========================
def inline_keyboard(rows: List[List[Tuple[str, str]]]) -> Dict:
    return {
        "inline_keyboard": [
            [{"text": t, "callback_data": d} for t, d in row]
            for row in rows
        ]
    }


def main_menu_kb(user_id: int = 0) -> Dict:
    rows = [
        [("🛠 خدمات ARYON", "svc_list"), ("🛒 ثبت سفارش", "order_start")],
        [("📁 نمونه‌کارها", "portfolio_list"), ("📦 پروژه‌های من", "my_orders")],
        [("📢 اخبار ARYON", "news_list"), ("❓ سوالات متداول", "faq_list")],
        [("🎧 پشتیبانی", "support"), ("ℹ️ درباره ARYON", "about")],
        [("👤 پروفایل من", "profile")],
    ]
    if user_id in ADMIN_IDS:
        rows.append([("👑 پنل مدیریت", "admin_panel")])
    return inline_keyboard(rows)


def services_kb(services: List[sqlite3.Row]) -> Dict:
    rows = []
    for s in services:
        rows.append([(s["name"], f"svc_detail:{s['id']}")])
    rows.append([("🔙 بازگشت", "main_menu")])
    return inline_keyboard(rows)


def order_type_kb() -> Dict:
    return inline_keyboard([
        [("🌐 سایت", "ord_type:سایت"), ("🤖 ربات", "ord_type:ربات")],
        [("📱 اپلیکیشن", "ord_type:اپلیکیشن"), ("⚙️ اتوماسیون", "ord_type:اتوماسیون")],
        [("🎨 طراحی", "ord_type:طراحی"), ("💻 پروژه اختصاصی", "ord_type:پروژه اختصاصی")],
        [("❓ سایر", "ord_type:سایر")],
        [("❌ لغو", "order_cancel")],
    ])


def order_budget_kb() -> Dict:
    return inline_keyboard([
        [("💰 زیر ۱ میلیون", "ord_budget:زیر ۱ میلیون")],
        [("💰 ۱ تا ۵ میلیون", "ord_budget:۱ تا ۵ میلیون")],
        [("💰 ۵ تا ۱۰ میلیون", "ord_budget:۵ تا ۱۰ میلیون")],
        [("💰 ۱۰ تا ۲۰ میلیون", "ord_budget:۱۰ تا ۲۰ میلیون")],
        [("💰 بالای ۲۰ میلیون", "ord_budget:بالای ۲۰ میلیون")],
        [("❓ هنوز مشخص نیست", "ord_budget:مشخص نیست")],
        [("❌ لغو", "order_cancel")],
    ])


def order_confirm_kb() -> Dict:
    return inline_keyboard([
        [("✅ تأیید و ثبت", "order_confirm")],
        [("✏️ ویرایش", "order_edit"), ("❌ لغو", "order_cancel")],
    ])


def my_orders_kb(orders: List[sqlite3.Row]) -> Dict:
    rows = []
    for o in orders:
        rows.append([(f"{o['order_code']} | {o['title']}", f"order_view:{o['order_code']}")])
    rows.append([("🔙 بازگشت", "main_menu")])
    return inline_keyboard(rows)


def order_status_label(status: str) -> str:
    labels = {
        "submitted": "🟡 ثبت شده",
        "reviewing": "🔎 در حال بررسی",
        "negotiating": "💬 در حال مذاکره",
        "approved": "🟢 تأیید شده",
        "in_progress": "⚙️ در حال توسعه",
        "testing": "🧪 در حال تست",
        "completed": "✅ تکمیل شده",
        "cancelled": "🔴 لغو شده",
    }
    return labels.get(status, status)


def admin_order_kb(order_code: str) -> Dict:
    return inline_keyboard([
        [("🔎 بررسی", f"adm_status:{order_code}:reviewing")],
        [("💬 مذاکره", f"adm_status:{order_code}:negotiating")],
        [("🟢 تأیید", f"adm_status:{order_code}:approved")],
        [("⚙️ در حال توسعه", f"adm_status:{order_code}:in_progress")],
        [("🧪 تست", f"adm_status:{order_code}:testing")],
        [("✅ تکمیل", f"adm_status:{order_code}:completed")],
        [("🔴 لغو", f"adm_status:{order_code}:cancelled")],
        [("💬 پیام به مشتری", f"adm_msg_order:{order_code}")],
    ])


def admin_panel_kb() -> Dict:
    return inline_keyboard([
        [("👥 کاربران", "adm_users"), ("📦 سفارش‌ها", "adm_orders")],
        [("🛠 خدمات", "adm_services"), ("📁 نمونه‌کارها", "adm_portfolio")],
        [("📢 اخبار", "adm_news"), ("❓ FAQ", "adm_faq")],
        [("📊 آمار", "adm_stats"), ("📨 پیام همگانی", "adm_broadcast")],
        [("✉️ پیام به کاربر", "adm_msg_user")],
        [("⚙️ تنظیمات", "adm_settings")],
        [("🔙 بازگشت", "main_menu")],
    ])


# =========================
# USER SYSTEM
# =========================
async def ensure_user(user_id: int, first_name: str, username: Optional[str]) -> sqlite3.Row:
    now = datetime.utcnow().isoformat()
    row = await db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
    if row is None:
        await db_execute(
            "INSERT INTO users (user_id, first_name, username, join_date, last_activity, is_blocked) VALUES (?,?,?,?,?,0)",
            (user_id, first_name, username or "", now, now),
        )
        log.info("New user registered: %s (%s)", user_id, first_name)
        return await db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
    else:
        await db_execute(
            "UPDATE users SET first_name=?, username=?, last_activity=? WHERE user_id=?",
            (first_name, username or "", now, user_id),
        )
        return row


async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def is_blocked(user_id: int) -> bool:
    row = await db_fetchone("SELECT is_blocked FROM users WHERE user_id=?", (user_id,))
    return bool(row and row["is_blocked"])


def rate_limit_ok(user_id: int) -> bool:
    now = time.time()
    stamps = _rate_limit_store.get(user_id, [])
    stamps = [t for t in stamps if now - t < RATE_LIMIT_WINDOW]
    if len(stamps) >= RATE_LIMIT_MAX:
        return False
    stamps.append(now)
    _rate_limit_store[user_id] = stamps
    return True


# =========================
# ORDER SYSTEM
# =========================
_order_states: Dict[int, Dict[str, Any]] = {}


async def generate_order_code() -> str:
    year = date.today().year
    row = await db_fetchone("SELECT COUNT(*) as c FROM orders")
    count = (row["c"] if row else 0) + 1
    return f"ARYON-{year}-{count:06d}"


async def create_order(user_id: int, data: Dict[str, str]) -> str:
    code = await generate_order_code()
    now = datetime.utcnow().isoformat()
    await db_execute(
        """INSERT INTO orders (order_code, user_id, project_type, title, description, features,
           budget, deadline, contact, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code, user_id, data.get("type", ""), data.get("title", ""),
         data.get("description", ""), data.get("features", ""),
         data.get("budget", ""), data.get("deadline", ""), data.get("contact", ""),
         "submitted", now, now),
    )
    log.info("Order created: %s by user %s", code, user_id)
    return code


async def notify_admins_order(order_code: str, user_id: int) -> None:
    order = await db_fetchone("SELECT * FROM orders WHERE order_code=?", (order_code,))
    user = await db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not order or not user:
        return

    text = (
        "🆕 سفارش جدید ARYON\n\n"
        f"🆔 {order['order_code']}\n\n"
        f"👤 مشتری: {user['first_name']}\n"
        f"📞 تلفن: {user['phone'] or '—'}\n"
        f"🆔 User ID: `{user_id}`\n\n"
        f"🛠 نوع: {order['project_type']}\n"
        f"📌 عنوان: {order['title']}\n"
        f"📝 توضیحات: {order['description']}\n"
        f"⚙️ امکانات: {order['features']}\n"
        f"💰 بودجه: {order['budget']}\n"
        f"⏱ زمان: {order['deadline']}\n"
        f"📞 راه ارتباطی: {order['contact']}"
    )

    kb = admin_order_kb(order_code)
    for admin_id in ADMIN_IDS:
        await send_message(admin_id, text, kb)


async def notify_user_order_status(order_code: str) -> None:
    order = await db_fetchone("SELECT * FROM orders WHERE order_code=?", (order_code,))
    if not order:
        return
    text = (
        f"🔔 به‌روزرسانی وضعیت سفارش\n\n"
        f"🆔 {order['order_code']}\n"
        f"📌 {order['title']}\n"
        f"📊 وضعیت جدید: {order_status_label(order['status'])}"
    )
    await send_message(order["user_id"], text)


# =========================
# HANDLERS
# =========================
async def handle_start(chat_id: int, user_id: int, first_name: str, username: Optional[str]) -> None:
    user = await ensure_user(user_id, first_name, username)
    if not user["is_registered"]:
        # First time → ask for registration
        _order_states[user_id] = {"step": "REG_NAME", "data": {}}
        text = (
            "🤖 به ربات رسمی ARYON خوش آمدید!\n\n"
            "برای استفاده از خدمات، لطفاً ابتدا ثبت‌نام کنید.\n\n"
            "📝 لطفاً **نام و نام خانوادگی** خود را وارد کنید:"
        )
        await send_message(chat_id, text, inline_keyboard([[("❌ لغو", "cancel_reg")]]))
        return

    text = (
        "🤖 به ربات رسمی ARYON خوش آمدید!\n\n"
        "مرکز رسمی خدمات، پروژه‌ها و محصولات ARYON.\n\n"
        "از منوی زیر می‌توانید خدمات ما را مشاهده کنید، پروژه ثبت کنید، "
        "نمونه‌کارها را ببینید و وضعیت درخواست‌های خود را پیگیری کنید."
    )
    await send_message(chat_id, text, main_menu_kb(user_id))


async def handle_callback(chat_id: int, message_id: int, user_id: int, data: str, callback_query_id: str) -> None:
    await answer_callback(callback_query_id)

    # ---- Registration Cancel ----
    if data == "cancel_reg":
        _order_states.pop(user_id, None)
        await edit_message(chat_id, message_id, "❌ ثبت‌نام لغو شد.", main_menu_kb(user_id))
        return

    # ---- Main Menu ----
    if data == "main_menu":
        text = (
            "🤖 منوی اصلی ARYON\n\n"
            "گزینه موردنظر را انتخاب کنید:"
        )
        await edit_message(chat_id, message_id, text, main_menu_kb(user_id))
        return

    if data == "profile":
        user = await db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
        if not user:
            await edit_message(chat_id, message_id, "اطلاعات یافت نشد.", main_menu_kb(user_id))
            return
        orders_count = await db_fetchone("SELECT COUNT(*) as c FROM orders WHERE user_id=?", (user_id,))
        text = (
            f"👤 پروفایل من\n\n"
            f"📛 نام: {user['first_name']}\n"
            f"📞 تلفن: {user['phone'] or 'ثبت نشده'}\n"
            f"🆔 User ID: `{user_id}`\n"
            f"📅 عضویت: {user['join_date'][:10]}\n"
            f"📦 تعداد سفارش‌ها: {orders_count['c']}"
        )
        await edit_message(chat_id, message_id, text, inline_keyboard([[("✏️ ویرایش تلفن", "edit_phone")], [("🔙 بازگشت", "main_menu")]]))
        return

    if data == "edit_phone":
        _order_states[user_id] = {"step": "EDIT_PHONE", "data": {}}
        await edit_message(chat_id, message_id, "📞 شماره تلفن جدید خود را وارد کنید:\n\n(مثال: 09123456789)",
                           inline_keyboard([[("❌ لغو", "main_menu")]]))
        return

    # ---- Services ----
    if data == "svc_list":
        services = await db_fetchall("SELECT * FROM services WHERE is_active=1")
        if not services:
            await edit_message(chat_id, message_id, "🛠 خدمات ARYON\n\nدر حال حاضر خدماتی ثبت نشده است.", inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
            return
        text = "🛠 خدمات ARYON\n\nیک سرویس را انتخاب کنید:"
        await edit_message(chat_id, message_id, text, services_kb(services))
        return

    if data.startswith("svc_detail:"):
        svc_id = int(data.split(":")[1])
        s = await db_fetchone("SELECT * FROM services WHERE id=?", (svc_id,))
        if not s:
            await edit_message(chat_id, message_id, "سرویس یافت نشد.", inline_keyboard([[("🔙 بازگشت", "svc_list")]]))
            return
        text = (
            f"{s['name']}\n\n"
            f"📝 {s['description']}\n\n"
            f"⚙️ امکانات: {s['features']}\n"
            f"⏱ زمان تقریبی: {s['delivery_time']}\n\n"
            f"وضعیت: {'✅ فعال' if s['is_active'] else '❌ غیرفعال'}"
        )
        kb = inline_keyboard([[("🛒 ثبت سفارش", "order_start")], [("🔙 بازگشت", "svc_list")]])
        await edit_message(chat_id, message_id, text, kb)
        return

    # ---- Order Flow ----
    if data == "order_start":
        user = await db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
        if not user or not user["is_registered"]:
            await edit_message(chat_id, message_id, "⚠️ لطفاً ابتدا ثبت‌نام کنید. دستور /start را ارسال کنید.",
                               inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
            return
        _order_states[user_id] = {"step": "ORDER_TYPE", "data": {}}
        text = "🛒 ثبت سفارش جدید\n\nمرحله ۱ از ۷\n\nنوع پروژه خود را انتخاب کنید:"
        await edit_message(chat_id, message_id, text, order_type_kb())
        return

    if data.startswith("ord_type:"):
        val = data.split(":", 1)[1]
        st = _order_states.get(user_id)
        if not st:
            await edit_message(chat_id, message_id, "لطفاً دوباره شروع کنید.", inline_keyboard([[("🛒 ثبت سفارش", "order_start")]]))
            return
        st["data"]["type"] = val
        st["step"] = "ORDER_TITLE"
        await edit_message(chat_id, message_id,
                           "مرحله ۲ از ۷\n\n📌 عنوان پروژه را وارد کنید:\n\n(مثال: سایت فروشگاهی)",
                           inline_keyboard([[("❌ لغو", "order_cancel")]]))
        return

    if data.startswith("ord_budget:"):
        val = data.split(":", 1)[1]
        st = _order_states.get(user_id)
        if not st:
            await edit_message(chat_id, message_id, "لطفاً دوباره شروع کنید.", inline_keyboard([[("🛒 ثبت سفارش", "order_start")]]))
            return
        st["data"]["budget"] = val
        st["step"] = "ORDER_DEADLINE"
        await edit_message(chat_id, message_id,
                           "مرحله ۶ از ۷\n\n⏱ زمان موردنظر تحویل را وارد کنید:\n\n(مثال: تا ۳۰ روز)",
                           inline_keyboard([[("❌ لغو", "order_cancel")]]))
        return

    if data == "order_confirm":
        st = _order_states.get(user_id)
        if not st:
            await edit_message(chat_id, message_id, "اطلاعات یافت نشد.", inline_keyboard([[("🛒 ثبت سفارش", "order_start")]]))
            return
        code = await create_order(user_id, st["data"])
        del _order_states[user_id]
        await notify_admins_order(code, user_id)
        await edit_message(chat_id, message_id,
                           f"✅ سفارش شما با موفقیت ثبت شد!\n\n🆔 {code}\n\n"
                           f"وضعیت سفارش خود را از منوی «پروژه‌های من» پیگیری کنید.\n\n"
                           f"تیم ARYON در اسرع وقت با شما تماس خواهد گرفت.",
                           inline_keyboard([[("📦 پروژه‌های من", "my_orders")], [("🔙 بازگشت", "main_menu")]]))
        return

    if data == "order_edit":
        _order_states[user_id] = {"step": "ORDER_TYPE", "data": {}}
        await edit_message(chat_id, message_id, "✏️ ویرایش سفارش\n\nمرحله ۱ از ۷\n\nنوع پروژه:", order_type_kb())
        return

    if data == "order_cancel":
        _order_states.pop(user_id, None)
        await edit_message(chat_id, message_id, "❌ سفارش لغو شد.", main_menu_kb(user_id))
        return

    # ---- My Orders ----
    if data == "my_orders":
        orders = await db_fetchall("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
        if not orders:
            await edit_message(chat_id, message_id, "📦 پروژه‌های من\n\nشما هنوز سفارشی ثبت نکرده‌اید.",
                               inline_keyboard([[("🛒 ثبت سفارش", "order_start")], [("🔙 بازگشت", "main_menu")]]))
            return
        text = "📦 پروژه‌های من\n\nیک پروژه را انتخاب کنید:"
        await edit_message(chat_id, message_id, text, my_orders_kb(orders))
        return

    if data.startswith("order_view:"):
        code = data.split(":", 1)[1]
        o = await db_fetchone("SELECT * FROM orders WHERE order_code=? AND user_id=?", (code, user_id))
        if not o:
            await edit_message(chat_id, message_id, "پروژه یافت نشد.", inline_keyboard([[("🔙 بازگشت", "my_orders")]]))
            return
        text = (
            f"📋 جزئیات سفارش\n\n"
            f"🆔 {o['order_code']}\n"
            f"📌 {o['title']}\n"
            f"🛠 نوع: {o['project_type']}\n"
            f"📝 {o['description']}\n"
            f"⚙️ {o['features']}\n"
            f"💰 {o['budget']}\n"
            f"⏱ {o['deadline']}\n"
            f"📞 {o['contact']}\n\n"
            f"📊 وضعیت: {order_status_label(o['status'])}\n"
            f"📅 تاریخ: {o['created_at'][:10]}"
        )
        await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "my_orders")]]))
        return

    # ---- Portfolio ----
    if data == "portfolio_list":
        cats = await db_fetchall("SELECT DISTINCT category FROM portfolio")
        if not cats:
            await edit_message(chat_id, message_id, "📁 نمونه‌کارها\n\nهنوز نمونه‌ای ثبت نشده است.",
                               inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
            return
        rows = [[(c["category"], f"portfolio_cat:{c['category']}")] for c in cats]
        rows.append([("🔙 بازگشت", "main_menu")])
        await edit_message(chat_id, message_id, "📁 نمونه‌کارها\n\nدسته موردنظر را انتخاب کنید:", inline_keyboard(rows))
        return

    if data.startswith("portfolio_cat:"):
        cat = data.split(":", 1)[1]
        items = await db_fetchall("SELECT * FROM portfolio WHERE category=?", (cat,))
        if not items:
            await edit_message(chat_id, message_id, "نمونه‌ای در این دسته یافت نشد.",
                               inline_keyboard([[("🔙 بازگشت", "portfolio_list")]]))
            return
        text = f"📁 {cat}\n\n"
        for p in items:
            text += f"• {p['title']}\n  {p['technologies']}\n  {p['link'] or '—'}\n\n"
        await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "portfolio_list")]]))
        return

    # ---- News ----
    if data == "news_list":
        news = await db_fetchall("SELECT * FROM news WHERE published=1 ORDER BY id DESC LIMIT 10")
        if not news:
            await edit_message(chat_id, message_id, "📢 اخبار ARYON\n\nخبری منتشر نشده است.",
                               inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
            return
        text = "📢 اخبار ARYON\n\n"
        for n in news:
            text += f"📰 {n['title']}\n{n['content'][:200]}\n\n"
        await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
        return

    # ---- FAQ ----
    if data == "faq_list":
        faqs = await db_fetchall("SELECT * FROM faq")
        if not faqs:
            await edit_message(chat_id, message_id, "❓ سوالات متداول\n\nموردی ثبت نشده.",
                               inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
            return
        text = "❓ سوالات متداول\n\n"
        for f in faqs:
            text += f"{f['question']}\n{f['answer']}\n\n"
        await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
        return

    # ---- Support ----
    if data == "support":
        s = await db_fetchone("SELECT value FROM settings WHERE key='support_link'")
        link = s["value"] if s else SUPPORT_BOT_LINK
        text = (
            "🎧 پشتیبانی ARYON\n\n"
            "برای ارتباط با تیم پشتیبانی از لینک زیر استفاده کنید:\n\n"
            f"{link}\n\n"
            "یا سفارش خود را ثبت کنید تا با شما تماس بگیریم."
        )
        await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
        return

    # ---- About ----
    if data == "about":
        s = await db_fetchone("SELECT value FROM settings WHERE key='about_text'")
        text = s["value"] if s else "ℹ️ درباره ARYON\n\nARYON یک مجموعه فعال در حوزه توسعه و ساخت محصولات دیجیتال است."
        text += f"\n\n🎧 پشتیبانی: {SUPPORT_BOT_LINK}"
        await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "main_menu")]]))
        return

    # ---- Admin Panel ----
    if await is_admin(user_id):
        if data == "admin_panel":
            await edit_message(chat_id, message_id, "👑 پنل مدیریت ARYON\n\nیک بخش را انتخاب کنید:", admin_panel_kb())
            return

        if data == "adm_users":
            row = await db_fetchone("SELECT COUNT(*) as c FROM users")
            today = date.today().isoformat()
            new_today = await db_fetchone("SELECT COUNT(*) as c FROM users WHERE join_date LIKE ?", (f"{today}%",))
            reg_count = await db_fetchone("SELECT COUNT(*) as c FROM users WHERE is_registered=1")
            text = (
                f"👥 مدیریت کاربران\n\n"
                f"کل کاربران: {row['c']}\n"
                f"ثبت‌نام شده: {reg_count['c']}\n"
                f"کاربران امروز: {new_today['c']}"
            )
            await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return

        if data == "adm_orders":
            orders = await db_fetchall("SELECT * FROM orders ORDER BY id DESC LIMIT 10")
            if not orders:
                await edit_message(chat_id, message_id, "📦 سفارش‌ها\n\nسفارشی ثبت نشده.",
                                   inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
                return
            rows = [[(f"{o['order_code']} | {o['title'][:20]}", f"adm_order_view:{o['order_code']}")] for o in orders]
            rows.append([("🔙 بازگشت", "admin_panel")])
            await edit_message(chat_id, message_id, "📦 سفارش‌ها:", inline_keyboard(rows))
            return

        if data.startswith("adm_order_view:"):
            code = data.split(":", 1)[1]
            o = await db_fetchone("SELECT * FROM orders WHERE order_code=?", (code,))
            if not o:
                await edit_message(chat_id, message_id, "سفارش یافت نشد.", inline_keyboard([[("🔙 بازگشت", "adm_orders")]]))
                return
            text = (
                f"📋 سفارش {o['order_code']}\n\n"
                f"👤 User: `{o['user_id']}`\n"
                f"🛠 {o['project_type']}\n"
                f"📌 {o['title']}\n"
                f"📝 {o['description']}\n"
                f"⚙️ {o['features']}\n"
                f"💰 {o['budget']}\n"
                f"⏱ {o['deadline']}\n"
                f"📞 {o['contact']}\n\n"
                f"📊 {order_status_label(o['status'])}"
            )
            await edit_message(chat_id, message_id, text, admin_order_kb(code))
            return

        if data.startswith("adm_status:"):
            parts = data.split(":", 2)
            code, new_status = parts[1], parts[2]
            now = datetime.utcnow().isoformat()
            await db_execute("UPDATE orders SET status=?, updated_at=? WHERE order_code=?", (new_status, now, code))
            await notify_user_order_status(code)
            await answer_callback(callback_query_id, f"وضعیت به {order_status_label(new_status)} تغییر کرد.")
            await edit_message(chat_id, message_id, f"✅ وضعیت {code} به {order_status_label(new_status)} تغییر کرد.", admin_order_kb(code))
            return

        if data.startswith("adm_msg_order:"):
            code = data.split(":", 1)[1]
            o = await db_fetchone("SELECT * FROM orders WHERE order_code=?", (code,))
            if not o:
                await edit_message(chat_id, message_id, "سفارش یافت نشد.", inline_keyboard([[("🔙 بازگشت", "adm_orders")]]))
                return
            _order_states[user_id] = {"step": "ADMIN_MSG_ORDER", "data": {"order_code": code, "target_user": o["user_id"]}}
            await edit_message(chat_id, message_id,
                               f"💬 ارسال پیام به مشتری سفارش {code}\n\nلطفاً متن پیام خود را ارسال کنید:",
                               inline_keyboard([[("❌ لغو", "admin_panel")]]))
            return

        if data == "adm_stats":
            total_users = (await db_fetchone("SELECT COUNT(*) as c FROM users"))["c"]
            reg_users = (await db_fetchone("SELECT COUNT(*) as c FROM users WHERE is_registered=1"))["c"]
            today = date.today().isoformat()
            new_users = (await db_fetchone("SELECT COUNT(*) as c FROM users WHERE join_date LIKE ?", (f"{today}%",)))["c"]
            total_orders = (await db_fetchone("SELECT COUNT(*) as c FROM orders"))["c"]
            today_orders = (await db_fetchone("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{today}%",)))["c"]
            active = (await db_fetchone("SELECT COUNT(*) as c FROM orders WHERE status IN ('in_progress','testing','approved')"))["c"]
            completed = (await db_fetchone("SELECT COUNT(*) as c FROM orders WHERE status='completed'"))["c"]
            cancelled = (await db_fetchone("SELECT COUNT(*) as c FROM orders WHERE status='cancelled'"))["c"]

            by_type = await db_fetchall("SELECT project_type, COUNT(*) as c FROM orders GROUP BY project_type")
            type_lines = "\n".join([f"  • {r['project_type']}: {r['c']}" for r in by_type])

            text = (
                f"📊 داشبورد آماری ARYON\n\n"
                f"👥 کل کاربران: {total_users}\n"
                f"✅ ثبت‌نام شده: {reg_users}\n"
                f"🆕 کاربران امروز: {new_users}\n\n"
                f"📦 کل سفارش‌ها: {total_orders}\n"
                f"📦 سفارش‌های امروز: {today_orders}\n"
                f"⚙️ پروژه‌های فعال: {active}\n"
                f"✅ تکمیل‌شده: {completed}\n"
                f"🔴 لغو‌شده: {cancelled}\n\n"
                f"📈 سفارش بر اساس نوع:\n{type_lines or '  —'}"
            )
            await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return

        if data == "adm_broadcast":
            await edit_message(chat_id, message_id,
                               "📨 پیام همگانی\n\nلطفاً متن پیام خود را ارسال کنید:",
                               inline_keyboard([[("❌ لغو", "admin_panel")]]))
            _order_states[user_id] = {"step": "ADMIN_BROADCAST", "data": {}}
            return

        if data == "adm_msg_user":
            _order_states[user_id] = {"step": "ADMIN_MSG_USER_ID", "data": {}}
            await edit_message(chat_id, message_id,
                               "✉️ ارسال پیام به کاربر خاص\n\nلطفاً **User ID** کاربر موردنظر را وارد کنید:",
                               inline_keyboard([[("❌ لغو", "admin_panel")]]))
            return

        if data == "adm_settings":
            s = await db_fetchone("SELECT value FROM settings WHERE key='support_link'")
            link = s["value"] if s else SUPPORT_BOT_LINK
            text = f"⚙️ تنظیمات\n\n🔗 لینک پشتیبانی: {link}\n\nبرای تغییر، از دستور `/set support_link <لینک>` استفاده کنید."
            await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return

        if data == "adm_services":
            services = await db_fetchall("SELECT * FROM services")
            text = "🛠 مدیریت خدمات\n\n"
            for s in services:
                text += f"• {s['name']} ({'✅' if s['is_active'] else '❌'})\n"
            text += "\nبرای مدیریت از دستورات استفاده کنید."
            await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return

        if data == "adm_portfolio":
            items = await db_fetchall("SELECT * FROM portfolio LIMIT 10")
            text = "📁 مدیریت نمونه‌کارها\n\n"
            for p in items:
                text += f"• {p['title']}\n"
            text += "\nبرای مدیریت از دستورات استفاده کنید."
            await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return

        if data == "adm_news":
            news = await db_fetchall("SELECT * FROM news ORDER BY id DESC LIMIT 10")
            text = "📢 مدیریت اخبار\n\n"
            for n in news:
                text += f"• {n['title']} ({'✅' if n['published'] else '⏳'})\n"
            text += "\nبرای مدیریت از دستورات استفاده کنید."
            await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return

        if data == "adm_faq":
            faqs = await db_fetchall("SELECT * FROM faq")
            text = "❓ مدیریت FAQ\n\n"
            for f in faqs:
                text += f"• {f['question']}\n"
            text += "\nبرای مدیریت از دستورات استفاده کنید."
            await edit_message(chat_id, message_id, text, inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return


async def handle_callback_broadcast(chat_id: int, message_id: int, user_id: int, data: str) -> None:
    """Handle broadcast confirmation callback."""
    if not await is_admin(user_id):
        return
    if data.startswith("adm_bc_confirm:"):
        st = _order_states.get(user_id)
        if not st or st["step"] != "ADMIN_BC_WAIT":
            await edit_message(chat_id, message_id, "خطا: پیامی یافت نشد.", inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
            return
        msg = st["data"]["message"]
        users = st["data"]["users"]
        del _order_states[user_id]
        sent, failed = 0, 0
        for uid in users:
            r = await send_message(uid, msg)
            if r and r.get("ok"):
                sent += 1
            else:
                failed += 1
        await edit_message(chat_id, message_id, f"✅ ارسال شد.\nموفق: {sent}\nناموفق: {failed}",
                           inline_keyboard([[("🔙 بازگشت", "admin_panel")]]))
        return


async def handle_message(chat_id: int, user_id: int, first_name: str, username: Optional[str], text: str) -> None:
    """Route text messages based on state."""
    if not rate_limit_ok(user_id):
        log.warning("Rate limit hit for user %s", user_id)
        return

    await ensure_user(user_id, first_name, username)

    if await is_blocked(user_id):
        await send_message(chat_id, "⛔ حساب شما مسدود شده است. برای اطلاعات بیشتر با پشتیبانی تماس بگیرید.")
        return

    # Commands
    if text.startswith("/start"):
        await handle_start(chat_id, user_id, first_name, username)
        return

    if text.startswith("/admin") and await is_admin(user_id):
        await send_message(chat_id, "👑 پنل مدیریت ARYON\n\nیک بخش را انتخاب کنید:", admin_panel_kb())
        return

    if text.startswith("/set ") and await is_admin(user_id):
        parts = text.split(" ", 2)
        if len(parts) == 3:
            key, value = parts[1], parts[2]
            await db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
            await send_message(chat_id, f"✅ تنظیم {key} به {value} تغییر کرد.")
        return

    # State-based handling
    st = _order_states.get(user_id)
    if st:
        step = st["step"]

        # ---- Registration Flow ----
        if step == "REG_NAME":
            st["data"]["name"] = text
            st["step"] = "REG_PHONE"
            await send_message(chat_id, "📞 لطفاً **شماره تلفن** خود را وارد کنید:\n\n(مثال: 09123456789)",
                               inline_keyboard([[("❌ لغو", "cancel_reg")]]))
            return

        elif step == "REG_PHONE":
            phone = re.sub(r'[^0-9+]', '', text)
            if len(phone) < 10:
                await send_message(chat_id, "⚠️ شماره تلفن نامعتبر است. لطفاً دوباره وارد کنید:")
                return
            st["data"]["phone"] = phone
            st["step"] = "REG_CONFIRM"
            name = st["data"]["name"]
            await send_message(chat_id,
                               f"📋 خلاصه اطلاعات ثبت‌نام\n\n"
                               f"📛 نام: {name}\n"
                               f"📞 تلفن: {phone}\n\n"
                               f"آیا اطلاعات صحیح است؟",
                               inline_keyboard([
                                   [("✅ تأیید ثبت‌نام", "reg_confirm")],
                                   [("✏️ ویرایش", "reg_edit"), ("❌ لغو", "cancel_reg")],
                               ]))
            return

        # ---- Edit Phone ----
        elif step == "EDIT_PHONE":
            phone = re.sub(r'[^0-9+]', '', text)
            if len(phone) < 10:
                await send_message(chat_id, "⚠️ شماره تلفن نامعتبر است. لطفاً دوباره وارد کنید:")
                return
            await db_execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))
            del _order_states[user_id]
            await send_message(chat_id, f"✅ شماره تلفن شما به {phone} به‌روزرسانی شد.", main_menu_kb(user_id))
            return

        # ---- Admin Broadcast ----
        elif step == "ADMIN_BROADCAST":
            users = await db_fetchall("SELECT user_id FROM users WHERE is_blocked=0")
            total = len(users)
            _order_states[user_id] = {"step": "ADMIN_BC_WAIT", "data": {"message": text, "users": [u["user_id"] for u in users]}}
            await send_message(chat_id, f"⚠️ پیام همگانی\n\nاین پیام برای {total} کاربر ارسال خواهد شد.\n\nآیا ادامه می‌دهید؟",
                               inline_keyboard([[("✅ تأیید ارسال", f"adm_bc_confirm:{total}")], [("❌ لغو", "admin_panel")]]))
            return

        # ---- Admin Message to Specific User ----
        elif step == "ADMIN_MSG_USER_ID":
            target = text.strip()
            if not target.isdigit():
                await send_message(chat_id, "⚠️ User ID نامعتبر است. لطفاً یک عدد وارد کنید:")
                return
            target_id = int(target)
            target_user = await db_fetchone("SELECT * FROM users WHERE user_id=?", (target_id,))
            if not target_user:
                await send_message(chat_id, f"⚠️ کاربر با ID `{target_id}` یافت نشد.")
                return
            _order_states[user_id] = {"step": "ADMIN_MSG_USER_TEXT", "data": {"target_user": target_id, "target_name": target_user["first_name"]}}
            await send_message(chat_id,
                               f"✉️ ارسال پیام به {target_user['first_name']} (`{target_id}`)\n\n"
                               f"لطفاً متن پیام خود را ارسال کنید:",
                               inline_keyboard([[("❌ لغو", "admin_panel")]]))
            return

        elif step == "ADMIN_MSG_USER_TEXT":
            target_id = st["data"]["target_user"]
            del _order_states[user_id]
            result = await send_message(target_id, f"📩 پیام از مدیریت ARYON:\n\n{text}")
            if result and result.get("ok"):
                await send_message(chat_id, f"✅ پیام با موفقیت به `{target_id}` ارسال شد.", admin_panel_kb())
            else:
                await send_message(chat_id, f"❌ خطا در ارسال پیام به `{target_id}`.", admin_panel_kb())
            return

        elif step == "ADMIN_MSG_ORDER":
            target_id = st["data"]["target_user"]
            order_code = st["data"]["order_code"]
            del _order_states[user_id]
            result = await send_message(target_id, f"📩 پیام درباره سفارش {order_code}:\n\n{text}")
            if result and result.get("ok"):
                await send_message(chat_id, f"✅ پیام به مشتری سفارش {order_code} ارسال شد.", admin_panel_kb())
            else:
                await send_message(chat_id, f"❌ خطا در ارسال پیام.", admin_panel_kb())
            return

        # ---- Order Flow ----
        elif step == "ORDER_TITLE":
            st["data"]["title"] = text
            st["step"] = "ORDER_DESCRIPTION"
            await send_message(chat_id, "مرحله ۳ از ۷\n\n📝 توضیحات کامل پروژه را وارد کنید:",
                               inline_keyboard([[("❌ لغو", "order_cancel")]]))
            return

        elif step == "ORDER_DESCRIPTION":
            st["data"]["description"] = text
            st["step"] = "ORDER_FEATURES"
            await send_message(chat_id, "مرحله ۴ از ۷\n\n⚙️ امکانات موردنیاز را وارد کنید:",
                               inline_keyboard([[("❌ لغو", "order_cancel")]]))
            return

        elif step == "ORDER_FEATURES":
            st["data"]["features"] = text
            st["step"] = "ORDER_BUDGET"
            await send_message(chat_id, "مرحله ۵ از ۷\n\n💰 بودجه خود را انتخاب کنید:", order_budget_kb())
            return

        elif step == "ORDER_DEADLINE":
            st["data"]["deadline"] = text
            st["step"] = "ORDER_CONTACT"
            await send_message(chat_id, "مرحله ۷ از ۷\n\n📞 راه ارتباطی (شماره تماس یا آیدی) را وارد کنید:",
                               inline_keyboard([[("❌ لغو", "order_cancel")]]))
            return

        elif step == "ORDER_CONTACT":
            st["data"]["contact"] = text
            d = st["data"]
            summary = (
                "📋 خلاصه درخواست\n\n"
                f"🛠 نوع پروژه: {d.get('type','—')}\n\n"
                f"📌 عنوان: {d.get('title','—')}\n\n"
                f"📝 توضیحات: {d.get('description','—')}\n\n"
                f"⚙️ امکانات: {d.get('features','—')}\n\n"
                f"💰 بودجه: {d.get('budget','—')}\n\n"
                f"⏱ زمان: {d.get('deadline','—')}\n\n"
                f"📞 راه ارتباطی: {d.get('contact','—')}"
            )
            await send_message(chat_id, summary, order_confirm_kb())
            return


async def handle_reg_confirm(chat_id: int, message_id: int, user_id: int) -> None:
    """Finalize user registration."""
    st = _order_states.get(user_id)
    if not st or st["step"] != "REG_CONFIRM":
        return
    name = st["data"]["name"]
    phone = st["data"]["phone"]
    await db_execute(
        "UPDATE users SET first_name=?, phone=?, is_registered=1 WHERE user_id=?",
        (name, phone, user_id),
    )
    del _order_states[user_id]
    text = (
        "✅ ثبت‌نام شما با موفقیت انجام شد!\n\n"
        f"📛 نام: {name}\n"
        f"📞 تلفن: {phone}\n\n"
        "حالا می‌توانید از تمام خدمات ARYON استفاده کنید."
    )
    await edit_message(chat_id, message_id, text, main_menu_kb(user_id))


# =========================
# WEBHOOK
# =========================
async def process_update(update: Dict[str, Any]) -> None:
    try:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            from_user = msg.get("from", {})
            user_id = from_user.get("id")
            if user_id is None:
                return
            first_name = from_user.get("first_name", "")
            username = from_user.get("username")
            text = msg.get("text", "")
            if text:
                await handle_message(chat_id, user_id, first_name, username, text)

        elif "callback_query" in update:
            cq = update["callback_query"]
            cb_id = cq["id"]
            from_user = cq.get("from", {})
            user_id = from_user.get("id")
            if user_id is None:
                return
            msg = cq.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            message_id = msg.get("message_id")
            data = cq.get("data", "")

            # Handle registration confirm
            if data == "reg_confirm":
                await answer_callback(cb_id)
                await handle_reg_confirm(chat_id, message_id, user_id)
                return

            if data == "reg_edit":
                await answer_callback(cb_id)
                _order_states[user_id] = {"step": "REG_NAME", "data": {}}
                await edit_message(chat_id, message_id, "📝 لطفاً **نام و نام خانوادگی** خود را دوباره وارد کنید:",
                                   inline_keyboard([[("❌ لغو", "cancel_reg")]]))
                return

            if chat_id and message_id:
                await handle_callback(chat_id, message_id, user_id, data, cb_id)
                if data.startswith("adm_bc_confirm:"):
                    await handle_callback_broadcast(chat_id, message_id, user_id, data)
    except Exception as e:
        log.exception("Error processing update: %s", e)


# =========================
# SERVER
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if WEBHOOK_URL:
        base = WEBHOOK_URL.rstrip("/")
        await set_webhook(f"{base}/webhook")
    yield
    if _http_client:
        await _http_client.aclose()


app = FastAPI(title="ARYON Bot", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "ARYON Bot is running."}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ARYON Bot"}


@app.post("/webhook")
async def webhook(request: Request, x_bale_webhook_secret: Optional[str] = Header(None, alias="X-Bale-Webhook-Secret")):
    if WEBHOOK_SECRET:
        if x_bale_webhook_secret != WEBHOOK_SECRET:
            log.warning("Invalid webhook secret.")
            raise HTTPException(status_code=403, detail="Forbidden")

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Invalid update")

    await process_update(update)
    return JSONResponse({"ok": True})


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    import uvicorn
    if not BALE_BOT_TOKEN:
        log.error("BALE_BOT_TOKEN is not set. Bot will not function.")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS is empty. Admin panel will be inaccessible.")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")