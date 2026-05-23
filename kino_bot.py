import json
import os
import logging
import asyncio
import re
import urllib.request
import urllib.error
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ChatMemberHandler
)

# ============= LOGGING =============
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= SOZLAMALAR =============
TOKEN = os.environ.get("BOT_TOKEN", "8679177935:AAHd2tcTrf_P0F7396UJjJXNjVjNkxL6lw0")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "7985206085").split(",") if x.strip()]
BOT_URL = "https://t.me/kino_livebot"
DATA_FILE = "kino_data.json"
STORAGE_CHANNEL_ID = os.environ.get("STORAGE_CHANNEL_ID", "-1003855167117")
KV_URL = os.environ.get("KV_REST_API_URL")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN")

_notified_admin = False

# ============= VERCEL KV (urllib - no extra deps) =============
async def kv_get(key):
    if not KV_URL or not KV_TOKEN:
        return None
    try:
        loop = asyncio.get_event_loop()
        def _do():
            req = urllib.request.Request(
                f"{KV_URL}/get/{key}",
                headers={"Authorization": f"Bearer {KV_TOKEN}"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                result = json.loads(r.read().decode()).get("result")
                return json.loads(result) if result else None
        return await loop.run_in_executor(None, _do)
    except Exception as e:
        logger.error(f"KV Get xato ({key}): {e}")
    return None

async def kv_set(key, value):
    if not KV_URL or not KV_TOKEN:
        return
    try:
        loop = asyncio.get_event_loop()
        payload = json.dumps(value, ensure_ascii=False).encode()
        def _do():
            req = urllib.request.Request(
                f"{KV_URL}/set/{key}",
                data=payload,
                headers={"Authorization": f"Bearer {KV_TOKEN}", "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        await loop.run_in_executor(None, _do)
    except Exception as e:
        logger.error(f"KV Set xato ({key}): {e}")

# ============= USER STATE (KV orqali, Vercel-safe) =============
async def get_state(user_id):
    d = await kv_get(f"state_{user_id}")
    if d and isinstance(d, dict):
        return d.get("state"), d.get("data", {})
    return None, {}

async def set_state(user_id, state, extra=None):
    await kv_set(f"state_{user_id}", {"state": state, "data": extra or {}})

# ============= MA'LUMOTLAR =============
data = {
    "kinolar": {},
    "guruhlar": [],
    "foydalanuvchilar": {},
    "statistika": {"jami_qidiruvlar": 0},
    "majburiy_kanallar": []
}

async def load_data():
    global data
    kv_data = await kv_get("kino_bot_data")
    if kv_data and isinstance(kv_data, dict):
        data.update(kv_data)
        data.setdefault("kinolar", {})
        data.setdefault("guruhlar", [])
        data.setdefault("foydalanuvchilar", {})
        data.setdefault("statistika", {"jami_qidiruvlar": 0})
        data.setdefault("majburiy_kanallar", [])
        logger.info("Ma'lumotlar KV dan yuklandi.")
        return
    # Mahalliy fayl
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                data.update(loaded)
                logger.info("Ma'lumotlar lokal fayldan yuklandi.")
                return
        except Exception as e:
            logger.error(f"Lokal fayl o'qishda xato: {e}")
    logger.info("Yangi bo'sh ma'lumotlar yaratildi.")

async def save_data():
    # Lokal
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lokal saqlashda xato: {e}")
    # KV
    await kv_set("kino_bot_data", data)

# ============= POST INIT =============
async def post_init(application: Application) -> None:
    global _notified_admin
    await load_data()
    if not _notified_admin:
        _notified_admin = True
        for aid in ADMIN_IDS:
            try:
                await application.bot.send_message(
                    chat_id=aid,
                    text="✅ *Bot muvaffaqiyatli yangilandi!* (v3.0 - Mukammal)\n\nBarcha tizimlar faol. 🚀",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Admin xabari yuborilmadi {aid}: {e}")

# ============= YORDAMCHI =============
async def register_user(user):
    uid = str(user.id)
    if uid not in data["foydalanuvchilar"]:
        data["foydalanuvchilar"][uid] = {
            "username": user.username or "",
            "first_name": user.first_name or "",
            "qoshilgan": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        await save_data()

async def check_subscription(user_id, bot):
    kanallar = data.get("majburiy_kanallar", [])
    if not kanallar:
        return True
    for ch in kanallar:
        try:
            cid = str(ch.get("chat_id", "")).strip()
            if not cid:
                continue
            member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception as e:
            logger.error(f"Obuna tekshirishda xato ({cid}): {e}")
            return "error"
    return True

def sub_keyboard():
    rows = []
    for ch in data.get("majburiy_kanallar", []):
        rows.append([InlineKeyboardButton(ch.get("name", "📢 Obuna bo'lish"), url=ch.get("url", BOT_URL))])
    rows.append([InlineKeyboardButton("✅ Obunani tasdiqlash", callback_data="check_sub")])
    return InlineKeyboardMarkup(rows)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Kino qo'shish", callback_data="admin_add")],
        [InlineKeyboardButton("🗑 Kino o'chirish", callback_data="admin_del")],
        [InlineKeyboardButton("📢 Kanal qo'shish", callback_data="admin_add_ch")],
        [InlineKeyboardButton("❌ Kanal o'chirish", callback_data="admin_del_ch")],
        [InlineKeyboardButton("📣 Reklama tarqatish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
    ])

# ============= START =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    await register_user(user)

    if chat.type != "private":
        return

    # Deep link orqali kod
    kod = context.args[0].strip() if context.args else None

    # Obuna tekshirish
    if data.get("majburiy_kanallar"):
        ok = await check_subscription(user.id, context.bot)
        if not ok:
            if kod:
                await set_state(user.id, "WAIT_SUB", {"kod": kod})
            await update.message.reply_text(
                f"👋 Salom *{user.first_name}*!\n\n"
                "⚠️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
                reply_markup=sub_keyboard(),
                parse_mode="Markdown"
            )
            return

    if kod:
        await send_movie(update, context, kod)
        return

    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            "👑 *Admin Panel*\n\nQuyidagi tugmalardan birini tanlang:",
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"🎬 *Assalomu alaykum {user.first_name}!*\n\n"
            "Kino kodini yozing — men darhol topib beraman.\n"
            "Yoki kino nomini yozib qidirishingiz ham mumkin! 🔍",
            parse_mode="Markdown"
        )

# ============= KINO YUBORISH =============
async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE, kod: str):
    user_id = update.effective_user.id
    kod = str(kod).strip()

    movie = data["kinolar"].get(kod)

    # Agar kod bo'yicha topilmasa — nom bo'yicha qidirish
    if not movie:
        matches = [
            (k, v) for k, v in data["kinolar"].items()
            if kod.lower() in v.get("desc", "").lower()
        ]
        if not matches:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ `{kod}` bo'yicha kino topilmadi.\n\nKodni yoki nomni to'g'ri kiriting.",
                parse_mode="Markdown"
            )
            return
        if len(matches) == 1:
            kod, movie = matches[0]
        else:
            txt = "🔎 *Quyidagi kinolar topildi:*\n\n"
            for k, v in matches[:10]:
                txt += f"▪️ `{k}` — {v.get('desc', '🎬')}\n"
            txt += "\nKino kodini yuboring."
            await context.bot.send_message(chat_id=user_id, text=txt, parse_mode="Markdown")
            return

    msg_id = movie["msg_id"] if isinstance(movie, dict) else movie
    chat_id = movie.get("chat_id", STORAGE_CHANNEL_ID) if isinstance(movie, dict) else STORAGE_CHANNEL_ID
    desc = movie.get("desc", "🎬 Kino") if isinstance(movie, dict) else "🎬 Kino"

    share_url = f"https://t.me/share/url?url=https://t.me/kino_livebot?start={kod}&text=🎬 {desc}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Do'stlarga ulashish", url=share_url)]])

    caption = f"🎬 *{desc}*\n🔑 Kod: `{kod}`\n\n👉 {BOT_URL}?start={kod}"

    status = await context.bot.send_message(chat_id=user_id, text="🔍 Kino yuklanmoqda...")
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=chat_id,
            message_id=msg_id,
            caption=caption,
            parse_mode="Markdown",
            protect_content=True,
            reply_markup=kb
        )
        await status.delete()
        data["statistika"]["jami_qidiruvlar"] = data["statistika"].get("jami_qidiruvlar", 0) + 1
        uid = str(user_id)
        if uid in data["foydalanuvchilar"]:
            data["foydalanuvchilar"][uid]["qidiruvlar"] = data["foydalanuvchilar"][uid].get("qidiruvlar", 0) + 1
        await save_data()
    except Exception as e:
        logger.error(f"Kino yuborishda xato: {e}")
        await status.edit_text("❌ Kino yuborishda xatolik. Kino o'chirilgan bo'lishi mumkin.")

# ============= MATN HANDLER =============
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    text = update.message.text.strip()
    state, sdata = await get_state(user.id)

    # ----- ADMIN HOLATLARI -----
    if user.id in ADMIN_IDS:
        if state == "WAIT_DEL_CODE":
            await set_state(user.id, None)
            if text in data["kinolar"]:
                del data["kinolar"][text]
                await save_data()
                await update.message.reply_text(f"✅ `{text}` kodli kino o'chirildi.", parse_mode="Markdown", reply_markup=admin_keyboard())
            else:
                await update.message.reply_text(f"❌ `{text}` kodli kino topilmadi.", parse_mode="Markdown", reply_markup=admin_keyboard())
            return

        elif state == "WAIT_CH_ID":
            await set_state(user.id, "WAIT_CH_URL", {"ch_id": text})
            await update.message.reply_text("2️⃣ Kanal invite linkini yuboring (masalan: `https://t.me/kanal`):")
            return

        elif state == "WAIT_CH_URL":
            await set_state(user.id, "WAIT_CH_NAME", {**sdata, "ch_url": text})
            await update.message.reply_text("3️⃣ Tugmada chiqadigan nomni yuboring (masalan: `🎬 Kino Kanal`):")
            return

        elif state == "WAIT_CH_NAME":
            new_ch = {"chat_id": sdata.get("ch_id"), "url": sdata.get("ch_url"), "name": text}
            data["majburiy_kanallar"].append(new_ch)
            await save_data()
            await set_state(user.id, None)
            await update.message.reply_text(f"✅ Kanal qo'shildi: *{text}*", parse_mode="Markdown", reply_markup=admin_keyboard())
            return

        elif state == "WAIT_AD_TEXT":
            await set_state(user.id, None)
            ad_text = update.message.text_html
            await update.message.reply_text("📣 Reklama tarqatilmoqda...")

            ok_gr = ok_usr = fail_gr = 0
            bad_groups = []
            for cid in list(data.get("guruhlar", [])):
                try:
                    await context.bot.send_message(chat_id=int(cid), text=ad_text, parse_mode="HTML")
                    ok_gr += 1
                    await asyncio.sleep(0.3)
                except:
                    fail_gr += 1
                    bad_groups.append(cid)

            for bad in bad_groups:
                if bad in data["guruhlar"]:
                    data["guruhlar"].remove(bad)
            if bad_groups:
                await save_data()

            for uid in list(data.get("foydalanuvchilar", {}).keys()):
                try:
                    await context.bot.send_message(chat_id=int(uid), text=ad_text, parse_mode="HTML")
                    ok_usr += 1
                    await asyncio.sleep(0.05)
                except:
                    pass

            await update.message.reply_text(
                f"📣 *Reklama yakunlandi:*\n\n"
                f"👥 Foydalanuvchilar: {ok_usr}\n"
                f"🏢 Guruhlar: {ok_gr}\n"
                f"❌ Chiqarib yuborilgan guruhlar: {fail_gr}",
                parse_mode="Markdown",
                reply_markup=admin_keyboard()
            )
            return

    # ----- FOYDALANUVCHI: OBUNA TEKSHIRISH -----
    if data.get("majburiy_kanallar"):
        ok = await check_subscription(user.id, context.bot)
        if not ok:
            await set_state(user.id, "WAIT_SUB", {"kod": text})
            await update.message.reply_text(
                "⚠️ Avval kanallarga obuna bo'ling!",
                reply_markup=sub_keyboard()
            )
            return

    await send_movie(update, context, text)

# ============= MEDIA HANDLER (ADMIN KINO YUKLASH) =============
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return

    caption = update.message.caption or ""

    if not caption.strip():
        await update.message.reply_text(
            "⚠️ *Kino qo'shish uchun:*\n\n"
            "Videoni yuboring va uning *izoh (podpis)* qismiga kino kodini va nomini yozing.\n\n"
            "*Format:* `KOD Kino nomi`\n"
            "*Misol:* `284 Anakonda`\n\n"
            "Yoki ID belgisi bilan:\n"
            "`ID: 284\nAnakonda (1997)`",
            parse_mode="Markdown"
        )
        return

    # Kod ajratish: ID/Kod dan keyin kelgan raqamni ustivor olish
    kod = None
    desc = caption.strip()

    # 1. "ID: 284" yoki "Kod: 284" formatini sinash (raqamli)
    m = re.search(r'(?i)(?:id|kod|kodi)\s*[:\-]?\s*(\d+)', caption)
    if m:
        kod = m.group(1)
        desc = re.sub(r'(?i)(?:id|kod|kodi)\s*[:\-]?\s*\d+', '', caption).strip()

    # 2. Agar topilmasa — har qanday ID kalit so'zi bilan alfanumerik
    if not kod:
        m = re.search(r'(?i)(?:id|kod|kodi)\s*[:\-]?\s*([A-Za-z0-9]+)', caption)
        if m:
            kod = m.group(1)
            desc = re.sub(r'(?i)(?:id|kod|kodi)\s*[:\-]?\s*[A-Za-z0-9]+', '', caption).strip()

    # 3. Agar kalit so'z yo'q bo'lsa — birinchi token raqam bo'lsa uni kod deb ol
    if not kod:
        parts = caption.strip().split(None, 1)
        if parts and re.fullmatch(r'\d+', parts[0]):
            kod = parts[0]
            desc = parts[1].strip() if len(parts) > 1 else "🎬 Kino"

    # 4. Oxirgi fallback: birinchi so'z
    if not kod:
        parts = caption.strip().split(None, 1)
        kod = parts[0]
        desc = parts[1].strip() if len(parts) > 1 else "🎬 Kino"

    if not desc:
        desc = caption.strip()

    # Kinoni storage kanalga nusxalash
    status = await update.message.reply_text(f"📥 Kino `{kod}` kodi bilan saqlanmoqda...", parse_mode="Markdown")
    try:
        if STORAGE_CHANNEL_ID:
            sent = await context.bot.copy_message(
                chat_id=STORAGE_CHANNEL_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            store_chat = STORAGE_CHANNEL_ID
            store_msg = sent.message_id
        else:
            store_chat = update.message.chat_id
            store_msg = update.message.message_id

        data["kinolar"][str(kod)] = {
            "msg_id": store_msg,
            "chat_id": store_chat,
            "desc": desc
        }
        await save_data()
        await status.delete()

        await update.message.reply_text(
            f"✅ *Kino saqlandi!*\n\n"
            f"🔑 Kod: `{kod}`\n"
            f"📝 Nomi: {desc}",
            parse_mode="Markdown",
            reply_markup=admin_keyboard()
        )
        # Reklama tarqatish
        await broadcast_new_movie(context, kod, desc)

    except Exception as e:
        logger.error(f"Kino saqlashda xato: {e}")
        await status.edit_text(f"❌ Xatolik: {e}")

# ============= YANGI KINO REKLAMASI =============
async def broadcast_new_movie(context, kod, desc):
    msg = (
        f"🎬 *YANGI KINO!*\n\n"
        f"📝 {desc}\n"
        f"🔑 Kod: `{kod}`\n\n"
        f"👉 {BOT_URL}?start={kod}"
    )
    for cid in list(data.get("guruhlar", [])):
        try:
            await context.bot.send_message(chat_id=int(cid), text=msg, parse_mode="Markdown")
            await asyncio.sleep(0.3)
        except:
            pass
    for uid in list(data.get("foydalanuvchilar", {}).keys()):
        try:
            await context.bot.send_message(chat_id=int(uid), text=msg, parse_mode="Markdown")
            await asyncio.sleep(0.05)
        except:
            pass

# ============= CALLBACK HANDLER =============
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    cb = q.data

    # Obuna tasdiqlash
    if cb == "check_sub":
        ok = await check_subscription(uid, context.bot)
        if ok is True:
            state, sdata = await get_state(uid)
            kod = sdata.get("kod") if sdata else None
            await set_state(uid, None)
            if kod:
                try:
                    await q.message.delete()
                except:
                    pass
                await send_movie(update, context, kod)
            else:
                await q.edit_message_text("✅ Obuna tasdiqlandi! Kino kodini yuboring.")
        elif ok == "error":
            await q.message.reply_text("❌ Xatolik: Bot kanalda admin emas.")
        else:
            await q.message.reply_text("❌ Hali obuna bo'lmadingiz!")
        return

    # Admin tugmalari
    if uid not in ADMIN_IDS:
        return

    if cb == "admin_add":
        await q.message.reply_text(
            "🎬 *Kino qo'shish:*\n\n"
            "Videoni yuboring va podpisiga quyidagi formatda yozing:\n"
            "`284 Anakonda (1997)` yoki `ID:284 Anakonda`",
            parse_mode="Markdown"
        )

    elif cb == "admin_del":
        await set_state(uid, "WAIT_DEL_CODE")
        await q.message.reply_text("🗑 O'chirmoqchi bo'lgan kino *kodini* yuboring:", parse_mode="Markdown")

    elif cb == "admin_add_ch":
        await set_state(uid, "WAIT_CH_ID")
        await q.message.reply_text("1️⃣ Kanal ID yoki @username kiriting:")

    elif cb == "admin_del_ch":
        kanallar = data.get("majburiy_kanallar", [])
        if not kanallar:
            await q.message.reply_text("Hozircha majburiy kanal yo'q.", reply_markup=admin_keyboard())
            return
        rows = [[InlineKeyboardButton(f"❌ {ch['name']}", callback_data=f"del_ch_{i}")] for i, ch in enumerate(kanallar)]
        rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])
        await q.message.reply_text("Qaysi kanalni o'chirasiz?", reply_markup=InlineKeyboardMarkup(rows))

    elif cb == "admin_broadcast":
        await set_state(uid, "WAIT_AD_TEXT")
        await q.message.reply_text("📝 Reklama matnini yuboring (HTML formati qo'llab-quvvatlanadi):")

    elif cb == "admin_stats":
        kinolar = len(data.get("kinolar", {}))
        users = len(data.get("foydalanuvchilar", {}))
        groups = len(data.get("guruhlar", []))
        searches = data.get("statistika", {}).get("jami_qidiruvlar", 0)
        channels = len(data.get("majburiy_kanallar", []))
        await q.message.reply_text(
            f"📊 *Statistika:*\n\n"
            f"🎬 Kinolar: {kinolar} ta\n"
            f"👤 Foydalanuvchilar: {users} ta\n"
            f"🏢 Guruhlar: {groups} ta\n"
            f"📢 Majburiy kanallar: {channels} ta\n"
            f"🔍 Jami qidiruvlar: {searches} ta",
            parse_mode="Markdown",
            reply_markup=admin_keyboard()
        )

    elif cb == "admin_back":
        await set_state(uid, None)
        await q.edit_message_text("👑 *Admin Panel*", parse_mode="Markdown", reply_markup=admin_keyboard())

    elif cb.startswith("del_ch_"):
        idx = int(cb.split("_")[2])
        kanallar = data.get("majburiy_kanallar", [])
        if 0 <= idx < len(kanallar):
            removed = kanallar.pop(idx)
            await save_data()
            await q.edit_message_text(f"✅ *{removed['name']}* o'chirildi.", parse_mode="Markdown", reply_markup=admin_keyboard())

# ============= GURUH KUZATISH =============
async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result:
        return
    cid = str(result.chat.id)
    ctype = result.chat.type
    status = result.new_chat_member.status

    if ctype in ["group", "supergroup", "channel"]:
        if status in [ChatMember.ADMINISTRATOR, ChatMember.MEMBER]:
            if cid not in data["guruhlar"]:
                data["guruhlar"].append(cid)
                await save_data()
        elif status in [ChatMember.LEFT, ChatMember.KICKED, ChatMember.RESTRICTED]:
            if cid in data["guruhlar"]:
                data["guruhlar"].remove(cid)
                await save_data()

# ============= APPLICATION =============
def build_application():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(
        (filters.VIDEO | filters.Document.ALL | filters.PHOTO | filters.AUDIO | filters.ANIMATION),
        handle_media
    ))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    return app

async def main():
    app = build_application()
    logger.info("Bot polling boshlanmoqda...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
