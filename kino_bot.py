import json
import os
import logging
import asyncio
import re
import urllib.request
import urllib.error
import html
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
    CallbackQueryHandler, ChatMemberHandler
)
import aiohttp

# ============= LOGGING =============
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= SOZLAMALAR =============
TOKEN = os.environ.get("BOT_TOKEN", "8679177935:AAEDtJ7vHKzhJV1HTkr5BMWscPhKdzn43UQ")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "7985206085").split(",") if x.strip()]
BOT_URL = "https://t.me/kino_livebot"
DATA_FILE = "kino_data.json"
STORAGE_CHANNEL_ID = os.environ.get("STORAGE_CHANNEL_ID", "-1003855167117")

# MongoDB
MONGO_URL = os.environ.get("MONGO_URL", "mongodb+srv://bilol006:bilol006@cluster0.y0pbpop.mongodb.net/?appName=Cluster0")

# SSL/TLS xatoliklarini oldini olish uchun parametrlar qo'shamiz
if "tls=" not in MONGO_URL.lower():
    separator = "&" if "?" in MONGO_URL else "?"
    MONGO_URL += f"{separator}tls=true&tlsAllowInvalidCertificates=true&retryWrites=true&w=majority"

from motor.motor_asyncio import AsyncIOMotorClient
db_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = db_client["kino_bot_db"]
movies_col = db["movies"]
users_col = db["users"]
config_col = db["config"]
states_col = db["states"]

_notified_admin = False

# KV O'chirildi (MongoDB ishlatilmoqda)

# ============= USER STATE (MONGODB) =============
async def get_state(user_id):
    try:
        d = await states_col.find_one({"_id": str(user_id)})
        if d:
            return d.get("state"), d.get("data", {})
    except Exception as e:
        logger.error(f"State olishda xato ({user_id}): {e}")
    return None, {}

async def set_state(user_id, state, extra=None):
    try:
        if state is None:
            await states_col.delete_one({"_id": str(user_id)})
        else:
            await states_col.update_one(
                {"_id": str(user_id)},
                {"$set": {"state": state, "data": extra or {}}},
                upsert=True
            )
    except Exception as e:
        logger.error(f"State saqlashda xato ({user_id}): {e}")

# ============= MA'LUMOTLAR VA PERSISTENCE (MONGODB) =============
cached_data = {
    "kinolar": {},
    "guruhlar": [],
    "statistika": {"jami_qidiruvlar": 0},
    "majburiy_kanallar": []
}

last_config_load = 0

async def load_data(force=False):
    """Barcha global sozlamalarni MongoDB dan yuklaydi"""
    global cached_data, last_config_load
    now = asyncio.get_event_loop().time()
    if not force and now - last_config_load < 60: # 60 soniya kesh
        return

    try:
        conf = await config_col.find_one({"_id": "main_config"})
        if conf:
            cached_data.update(conf)
            last_config_load = now
            logger.info("Konfiguratsiya MongoDB dan yuklandi.")
        elif os.path.exists(DATA_FILE):
            # ... (migratsiya kodi)
            pass
    except Exception as e:
        logger.error(f"MongoDB yuklashda xato: {e}")

async def save_config():
    """Asosiy konfiguratsiyani saqlaydi"""
    config = {
        "guruhlar": cached_data["guruhlar"],
        "majburiy_kanallar": cached_data["majburiy_kanallar"],
        "statistika": cached_data["statistika"]
    }
    try:
        await config_col.update_one({"_id": "main_config"}, {"$set": config}, upsert=True)
    except Exception as e:
        logger.error(f"Config saqlashda xato: {e}")

async def get_movie(kod):
    """Kinoni MongoDB dan oladi"""
    try:
        movie = await movies_col.find_one({"_id": str(kod)})
        return movie
    except Exception as e:
        logger.error(f"Kino olishda xato ({kod}): {e}")
    return None

async def save_movie(kod, movie_data):
    """Kinoni MongoDB ga saqlaydi"""
    try:
        await movies_col.update_one({"_id": str(kod)}, {"$set": movie_data}, upsert=True)
        # Umumiy statistika
        cached_data["statistika"]["jami_kinolar"] = await movies_col.count_documents({})
        await save_config()
    except Exception as e:
        logger.error(f"Kino saqlashda xato ({kod}): {e}")

async def delete_movie(kod):
    """Kinoni o'chiradi"""
    try:
        await movies_col.delete_one({"_id": str(kod)})
    except Exception as e:
        logger.error(f"Kino o'chirishda xato ({kod}): {e}")

# ============= POST INIT =============
async def post_init(application: Application) -> None:
    global _notified_admin
    # MongoDB ulanishini tekshirish
    try:
        # server_info() MongoDB ulanishini to'liq tekshiradi (handshake ham)
        await db_client.server_info()
        logger.info("MongoDB ulanishi muvaffaqiyatli!")
    except Exception as e:
        logger.critical(f"CRITICAL: MongoDB-ga ulanib bo'lmadi (Handshake failed?): {e}")
        # Error tafsilotlarini adminlarga yuboramiz
        for aid in ADMIN_IDS:
            try:
                await application.bot.send_message(
                    chat_id=aid,
                    text=f"❌ <b>DB Connection Error:</b>\n<code>{str(e)[:4000]}</code>\n\nIltimos, MongoDB Atlas Network Access (IP Whitelist) ni tekshiring!",
                    parse_mode="HTML"
                )
            except: pass
        
    await load_data()
    if not _notified_admin:
        _notified_admin = True
        for aid in ADMIN_IDS:
            try:
                await application.bot.send_message(
                    chat_id=aid,
                    text="✅ <b>Bot MongoDB bilan ishga tushdi!</b> (v4.5)\n\nTizimlar faol. 🚀",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Admin xabari yuborilmadi {aid}: {e}")

# ============= YORDAMCHI =============
async def fetch_movie_details(title):
    """Kino haqida qisqacha ma'lumot qidiradi (Yil va Reyting)"""
    try:
        # OMDb API (Free tier uchun apikey kerak, lekin asosan placeholder sifatida qoldiramiz)
        # Yoki oddiyroq search-dan foydalanish mumkin.
        # Bu yerda biz Title-dan yilni ajratib olishga harakat qilamiz
        year_match = re.search(r'\((\d{4})\)', title)
        year = year_match.group(1) if year_match else "Noma'lum"
        
        # Kelajakda bu yerda API chaqiruvini amalga oshirish mumkin
        return {"year": year, "rating": "IMDb: --"}
    except:
        return None

async def register_user(user):
    uid = str(user.id)
    try:
        await users_col.update_one(
            {"_id": uid},
            {"$setOnInsert": {
                "username": user.username or "",
                "first_name": user.first_name or "",
                "qoshilgan": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "qidiruvlar": 0
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"User ro'yxatga olishda xato: {e}")

async def check_subscription(user_id, bot):
    await load_data() # Yangi kanallarni tekshirish uchun keshni yangilaymiz
    kanallar = cached_data.get("majburiy_kanallar", [])
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
    for ch in cached_data.get("majburiy_kanallar", []):
        rows.append([InlineKeyboardButton(ch.get("name", "📢 Obuna bo'lish"), url=ch.get("url", BOT_URL))])
    rows.append([InlineKeyboardButton("✅ Obunani tasdiqlash", callback_data="check_sub")])
    return InlineKeyboardMarkup(rows)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Kino qo'shish", callback_data="admin_add"),
         InlineKeyboardButton("🗑 Kino o'chirish", callback_data="admin_del")],
        [InlineKeyboardButton("📢 Kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("❌ Kanal o'chirish", callback_data="admin_del_ch")],
        [InlineKeyboardButton("📣 Reklama tarqatish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📚 Kinolar ro'yxati", callback_data="admin_list_movies")],
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
    ])

# ============= START =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    await register_user(user)

    if chat.type != "private" and user.id not in ADMIN_IDS:
        return

    # Deep link orqali kod
    kod = context.args[0].strip() if context.args else None

    # Obuna tekshirish (faqat foydalanuvchilar uchun)
    if user.id not in ADMIN_IDS and cached_data.get("majburiy_kanallar"):
        ok = await check_subscription(user.id, context.bot)
        if ok is not True:
            if kod:
                await set_state(user.id, "WAIT_SUB", {"kod": kod})
            await update.message.reply_text(
                f"👋 Salom <b>{html.escape(user.first_name)}</b>!\n\n"
                "⚠️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
                reply_markup=sub_keyboard(),
                parse_mode="HTML"
            )
            return

    if kod:
        await send_movie(update, context, kod)
        return

    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            "👑 <b>Admin Panel</b>\n\nQuyidagi tugmalardan birini tanlang:",
            reply_markup=admin_keyboard(),
            parse_mode="HTML"
        )
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Kinolar ro'yxati", callback_data="list_movies")]])
        await update.message.reply_text(
            f"🎬 <b>Assalomu alaykum {html.escape(user.first_name)}!</b>\n\n"
            "Kino kodini yoki nomini yozing — men darhol topib beraman! 🔍",
            reply_markup=kb,
            parse_mode="HTML"
        )

# ============= KINO YUBORISH =============
async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE, kod: str):
    user_id = update.effective_user.id
    kod = str(kod).strip()

    movie = await get_movie(kod)

    # Agar kod bo'yicha topilmasa — nom bo'yicha qidirish
    if not movie:
        try:
            # MongoDB orqali tavsif bo'yicha qidirish (regex-ni escape qilib)
            import re as regex_lib
            safe_text = regex_lib.escape(kod)
            cursor = movies_col.find({"desc": {"$regex": safe_text, "$options": "i"}}).limit(10)
            matches = await cursor.to_list(length=10)
        except Exception as e:
            logger.error(f"Qidiruvda xato: {e}")
            matches = []

        if not matches:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ <code>{kod}</code> kodi yoki nomi bo'yicha kino topilmadi.\n\nKodni to'g'ri kiriting.",
                parse_mode="HTML"
            )
            return
        if len(matches) == 1:
            movie = matches[0]
            kod = movie["_id"]
        else:
            txt = "🔎 <b>Quyidagi kinolar topildi:</b>\n\n"
            for v in matches:
                txt += f"▪️ <code>{v['_id']}</code> — {v.get('desc', '🎬')}\n"
            txt += "\nKino kodini yuboring."
            await context.bot.send_message(chat_id=user_id, text=txt, parse_mode="HTML")
            return

    msg_id = movie["msg_id"]
    chat_id = movie.get("chat_id", STORAGE_CHANNEL_ID)
    desc = movie.get("desc", "🎬 Kino")

    # Kino ma'lumotlarini qidirish
    details = await fetch_movie_details(desc)
    extra_info = ""
    if details:
        extra_info = f"\n📅 Yil: {details['year']} | ⭐ {details['rating']}"

    share_url = f"https://t.me/share/url?url=https://t.me/kino_livebot?start={kod}&text=🎬 {desc}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Do'stlarga ulashish", url=share_url)]])

    # HTML yordamida caption tuzish
    safe_desc = html.escape(desc)
    caption = f"🎬 <b>{safe_desc}</b>{extra_info}\n🔑 Kod: <code>{kod}</code>\n\n👉 {BOT_URL}?start={kod}"

    status = await context.bot.send_message(chat_id=user_id, text="🔍 Kino yuklanmoqda...")
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=chat_id,
            message_id=msg_id,
            caption=caption,
            parse_mode="HTML",
            protect_content=True,
            reply_markup=kb
        )
        await status.delete()
        cached_data["statistika"]["jami_qidiruvlar"] = cached_data["statistika"].get("jami_qidiruvlar", 0) + 1
        
        # User statistikasini yangilash
        try:
            await users_col.update_one({"_id": str(user_id)}, {"$inc": {"qidiruvlar": 1}})
        except: pass
        
        await save_config()
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
            movie = await get_movie(text)
            if movie:
                await delete_movie(text)
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
            cached_data["majburiy_kanallar"].append(new_ch)
            await save_config()
            global last_config_load
            last_config_load = 0 # Keshni yangilashga majburlash
            await set_state(user.id, None)
            await update.message.reply_text(f"✅ Kanal qo'shildi: *{text}*", parse_mode="Markdown", reply_markup=admin_keyboard())
            return

        elif state == "WAIT_AD_TEXT":
            await set_state(user.id, None)
            ad_text = update.message.text_html
            status_msg = await update.message.reply_text("📣 Reklama tarqatish boshlandi...")

            ok_gr = ok_usr = fail_gr = fail_usr = 0
            
            # 1. Guruhlarga (Keshdan)
            for cid in list(cached_data.get("guruhlar", [])):
                try:
                    await context.bot.send_message(chat_id=int(cid), text=ad_text, parse_mode="HTML")
                    ok_gr += 1
                    await asyncio.sleep(0.3)
                except:
                    fail_gr += 1

            # 2. Barcha userlarga (MongoDB-dan)
            try:
                cursor = users_col.find({}, {"_id": 1})
                async for user_doc in cursor:
                    uid = user_doc["_id"]
                    try:
                        await context.bot.send_message(chat_id=int(uid), text=ad_text, parse_mode="HTML")
                        ok_usr += 1
                        await asyncio.sleep(0.05)
                    except:
                        fail_usr += 1
            except Exception as e:
                logger.error(f"Broadcast user error: {e}")

            await status_msg.edit_text(
                f"📣 <b>Reklama yakunlandi:</b>\n\n"
                f"👥 Foydalanuvchilar: <b>{ok_usr}</b> (Muvaffaqiyatli), {fail_usr} (Xato)\n"
                f"🏢 Guruhlar: <b>{ok_gr}</b> (Muvaffaqiyatli), {fail_gr} (Xato)",
                parse_mode="HTML"
            )
            await update.message.reply_text("Admin Panel:", reply_markup=admin_keyboard())
            return

    # ----- FOYDALANUVCHI: OBUNA TEKSHIRISH -----
    if cached_data.get("majburiy_kanallar"):
        ok = await check_subscription(user.id, context.bot)
        if ok is not True:
            await set_state(user.id, "WAIT_SUB", {"kod": text})
            await update.message.reply_text(
                "⚠️ Avval quyidagi kanallarga obuna bo'ling!",
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

    # Kod ajratish: FAQAT aniq markerlar bilan
    kod = None
    desc = caption.strip()

    # 1. "ID: 284" yoki "Kod: 284" formatini sinash
    m = re.search(r'(?i)\b(?:id|kodi|kod)\b\s*[:\-]?\s*([A-Za-z0-9]+)', caption)
    if m:
        kod = m.group(1)
        desc = re.sub(re.escape(m.group(0)), '', caption).strip()
    
    # 2. #123 yoki №123 formatini tekshirish
    if not kod:
        m = re.search(r'(?i)[#№]\s*([A-Za-z0-9]+)', caption)
        if m:
            kod = m.group(1)
            desc = re.sub(re.escape(m.group(0)), '', caption).strip()

    # FALLBACK YO'Q: Agar kod topilmasa, bot guessing qilmaydi.
    # Bu "ozi qoyilib qolyapti" muammosini hal qiladi.

    if not kod:
        await update.message.reply_text(
            "❌ <b>Kino kodini aniqlab bo'lmadi!</b>\n\n"
            "Iltimos, videoga izoh qilib quyidagilardan birini yozing:\n"
            "<code>Kod: 123 Nom</code> yoki <code>#123 Nom</code>",
            parse_mode="HTML"
        )
        return

    if not desc or desc == caption:
        desc = "🎬 Kino"
    else:
        # Kod topilgan bo'lsa, uni desc dan olib tashlashga harakat qilamiz
        pass

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

        movie_data = {
            "msg_id": store_msg,
            "chat_id": store_chat,
            "desc": desc
        }
        await save_movie(kod, movie_data)
        await status.delete()

        await update.message.reply_text(
            f"✅ <b>Kino saqlandi!</b>\n\n"
            f"🔑 Kod: <code>{kod}</code>\n"
            f"📝 Nomi: {html.escape(desc)}",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        # Reklama tarqatish
        await broadcast_new_movie(context, kod, desc)

    except Exception as e:
        logger.error(f"Kino saqlashda xato: {e}")
        await status.edit_text(f"❌ Xatolik: {e}")

# ============= YANGI KINO REKLAMASI =============
async def broadcast_new_movie(context, kod, desc):
    import html
    safe_desc = html.escape(desc)
    msg = (
        f"🎬 <b>YANGI KINO!</b>\n\n"
        f"📝 {safe_desc}\n"
        f"🔑 Kod: <code>{kod}</code>\n\n"
        f"👉 {BOT_URL}?start={kod}"
    )
    # Eslatma: Broadcast uchun barcha userlarni olish kerak. 
    # Hozirgi tuzilmada biz barcha user_id larni bilmaymiz.
    # Shuning uchun guruhlarga yuborish bilan cheklanamiz yoki 
    # user_id larni alohida listda saqlashimiz kerak.
    for cid in list(cached_data.get("guruhlar", [])):
        try:
            await context.bot.send_message(chat_id=int(cid), text=msg, parse_mode="HTML")
            await asyncio.sleep(0.3)
        except: pass

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
        kanallar = cached_data.get("majburiy_kanallar", [])
        if not kanallar:
            await q.message.reply_text("Hozircha majburiy kanal yo'q.", reply_markup=admin_keyboard())
            return
        rows = [[InlineKeyboardButton(f"❌ {ch['name']}", callback_data=f"del_ch_{i}")] for i, ch in enumerate(kanallar)]
        rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])
        await q.message.reply_text("Qaysi kanalni o'chirasiz?", reply_markup=InlineKeyboardMarkup(rows))

    elif cb == "admin_broadcast":
        await q.message.reply_text("⚠️ Eslatma: Hozircha reklama faqat guruhlarga tarqatiladi (Userlar ko'pligi sababli).")
        await set_state(uid, "WAIT_AD_TEXT")
        await q.message.reply_text("📝 Reklama matnini yuboring (HTML formatida):")

    elif cb == "admin_stats":
        try:
            kinolar = await movies_col.count_documents({})
            users = await users_col.count_documents({})
        except:
            kinolar = users = "Xato"
            
        groups = len(cached_data.get("guruhlar", []))
        searches = cached_data.get("statistika", {}).get("jami_qidiruvlar", 0)
        channels = len(cached_data.get("majburiy_kanallar", []))
        await q.edit_message_text(
            f"📊 <b>Statistika (MongoDB):</b>\n\n"
            f"🎬 Kinolar: {kinolar} ta\n"
            f"👤 Foydalanuvchilar: {users} ta\n"
            f"🏢 Guruhlar: {groups} ta\n"
            f"📢 Majburiy kanallar: {channels} ta\n"
            f"🔍 Jami qidiruvlar: {searches} ta",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )

    elif cb == "admin_list_movies" or cb == "list_movies":
        try:
            cursor = movies_col.find().sort("_id", -1).limit(30)
            movies = await cursor.to_list(length=30)
            if not movies:
                await q.message.reply_text("Kinolar mavjud emas.")
                return
            txt = "🎬 <b>Oxirgi qo'shilgan kinolar:</b>\n\n"
            for m in movies:
                txt += f"▪️ <code>{m['_id']}</code> — {html.escape(m.get('desc', '🎬'))}\n"
            
            if uid in ADMIN_IDS:
                await q.edit_message_text(txt, parse_mode="HTML", reply_markup=admin_keyboard())
            else:
                await q.message.reply_text(txt, parse_mode="HTML")
        except Exception as e:
            logger.error(f"List movies error: {e}")
            await q.message.reply_text("Xatolik yuz berdi.")

    elif cb == "admin_back":
        await set_state(uid, None)
        await q.edit_message_text("👑 *Admin Panel*", parse_mode="Markdown", reply_markup=admin_keyboard())

    elif cb.startswith("del_ch_"):
        idx = int(cb.split("_")[2])
        kanallar = cached_data.get("majburiy_kanallar", [])
        if 0 <= idx < len(kanallar):
            removed = kanallar.pop(idx)
            await save_config()
            global last_config_load
            last_config_load = 0
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
            if cid not in cached_data["guruhlar"]:
                cached_data["guruhlar"].append(cid)
                await save_config()
        elif status in [ChatMember.LEFT, ChatMember.KICKED, ChatMember.RESTRICTED]:
            if cid in cached_data["guruhlar"]:
                cached_data["guruhlar"].remove(cid)
                await save_config()

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
