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
TOKEN = os.environ.get("BOT_TOKEN", "9251899633:AAEIhZLLqh3_1TztkI8Rid36yX3YHok_6Vo")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "6762465157,7985206085").split(",") if x.strip()]
BOT_URL = "https://t.me/kino_liive_bot"
DATA_FILE = "kino_data.json"
STORAGE_CHANNEL_ID = os.environ.get("STORAGE_CHANNEL_ID", "-1003855167117")

# MongoDB
MONGO_URL = os.environ.get("MONGO_URL", "mongodb+srv://bilol006:bilol006@cluster0.43suzwf.mongodb.net/?retryWrites=true&w=majority")

db_client = None
db = None
movies_col = None
users_col = None
config_col = None
states_col = None

if MONGO_URL:
    try:
        url_lower = MONGO_URL.lower()
        params = []
        if "tls=" not in url_lower:
            params.append("tls=true")
        if "tlsallowinvalidcertificates=" not in url_lower:
            params.append("tlsAllowInvalidCertificates=true")
        if "retrywrites=" not in url_lower:
            params.append("retryWrites=true")
        if "w=" not in url_lower:
            params.append("w=majority")
        if params:
            separator = "&" if "?" in MONGO_URL else "?"
            MONGO_URL += separator + "&".join(params)
        from motor.motor_asyncio import AsyncIOMotorClient
        db_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = db_client["kino_bot_db"]
        movies_col = db["movies"]
        users_col = db["users"]
        config_col = db["config"]
        states_col = db["states"]
        logger.info("MongoDB ulanishi muvaffaqiyatli o'rnatildi.")
    except Exception as e:
        logger.warning(f"MongoDB ulanishda xato: {e}")
        db_client = None
        db = None
        movies_col = None
        users_col = None
        config_col = None
        states_col = None

_notified_admin = False
local_states = {}

# ============= USER STATE =============
async def get_state(user_id):
    if states_col is not None:
        try:
            d = await states_col.find_one({"_id": str(user_id)})
            if d:
                return d.get("state"), d.get("data", {})
        except Exception as e:
            logger.error(f"State olishda xato ({user_id}): {e}")
    st, dt = local_states.get(str(user_id), (None, {}))
    return st, dt

async def set_state(user_id, state, extra=None):
    if state is None:
        local_states.pop(str(user_id), None)
    else:
        local_states[str(user_id)] = (state, extra or {})
    if states_col is not None:
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
    "majburiy_kanallar": [],
    "kino_kanal": {}, # Shaxsiy / Rasmiy Kino Kanali
    "admin_sessions": {} # Adminlar uchun oxirgi kodlar (session)
}

last_config_load = 0

async def load_data(force=False):
    """Barcha global sozlamalarni MongoDB yoki JSON dan yuklaydi"""
    global cached_data, last_config_load
    now = asyncio.get_event_loop().time()
    if not force and now - last_config_load < 60:
        return

    # 1. JSON fayldan yuklash
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cached_data.update(saved)
        except Exception as e:
            logger.error(f"Fayldan yuklashda xato: {e}")

    # 2. Agar MongoDB mavjud bo'lsa
    if config_col is not None:
        try:
            conf = await config_col.find_one({"_id": "main_config"})
            if conf:
                cached_data.update(conf)
                last_config_load = now
                logger.info("Konfiguratsiya MongoDB dan yuklandi.")
        except Exception as e:
            logger.error(f"MongoDB yuklashda xato: {e}")

async def save_config():
    """Asosiy konfiguratsiyani saqlaydi"""
    config = {
        "guruhlar": cached_data.get("guruhlar", []),
        "majburiy_kanallar": cached_data.get("majburiy_kanallar", []),
        "kino_kanal": cached_data.get("kino_kanal", {}),
        "statistika": cached_data.get("statistika", {}),
        "kinolar": cached_data.get("kinolar", {}),
        "foydalanuvchilar": cached_data.get("foydalanuvchilar", {})
    }
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Config saqlashda xato: {e}")

    if config_col is not None:
        try:
            await config_col.update_one({"_id": "main_config"}, {"$set": config}, upsert=True)
        except Exception as e:
            logger.error(f"Config MongoDB-ga saqlashda xato: {e}")

async def get_movie(kod):
    """Kinoni oladi"""
    if movies_col is not None:
        try:
            movie = await movies_col.find_one({"_id": str(kod)})
            if movie:
                return movie
        except Exception as e:
            logger.error(f"Kino olishda xato ({kod}): {e}")
    return cached_data.get("kinolar", {}).get(str(kod))

async def save_movie(kod, movie_data):
    """Kinoni saqlaydi yoki mavjudiga qism qo'shadi"""
    part = movie_data.get("part", 1)
    new_episode = {
        "msg_id": movie_data["msg_id"],
        "chat_id": movie_data["chat_id"],
        "part": part
    }

    # Mahalliy kesh va JSON-ga saqlash
    existing = cached_data.setdefault("kinolar", {}).get(str(kod))
    if existing:
        episodes = existing.get("episodes", [])
        if not episodes and "msg_id" in existing:
            episodes = [{"msg_id": existing["msg_id"], "chat_id": existing["chat_id"], "part": 1}]
        updated = False
        for i, ep in enumerate(episodes):
            if ep.get("part") == part:
                episodes[i] = new_episode
                updated = True
                break
        if not updated:
            episodes.append(new_episode)
        episodes.sort(key=lambda x: x.get("part", 1))
        existing["episodes"] = episodes
        if "desc" in movie_data:
            existing["desc"] = movie_data["desc"]
    else:
        cached_data["kinolar"][str(kod)] = {
            "_id": str(kod),
            "desc": movie_data.get("desc", "🎬 Kino"),
            "episodes": [new_episode]
        }

    cached_data["statistika"]["jami_kinolar"] = len(cached_data["kinolar"])
    await save_config()

    if movies_col is not None:
        try:
            m_existing = await movies_col.find_one({"_id": str(kod)})
            if m_existing:
                episodes = m_existing.get("episodes", [])
                if not episodes and "msg_id" in m_existing:
                    episodes = [{"msg_id": m_existing["msg_id"], "chat_id": m_existing["chat_id"], "part": 1}]
                updated = False
                for i, ep in enumerate(episodes):
                    if ep.get("part") == part:
                        episodes[i] = new_episode
                        updated = True
                        break
                if not updated:
                    episodes.append(new_episode)
                episodes.sort(key=lambda x: x.get("part", 1))
                await movies_col.update_one(
                    {"_id": str(kod)},
                    {"$set": {"episodes": episodes, "desc": movie_data.get("desc", m_existing.get("desc"))}}
                )
            else:
                await movies_col.insert_one({
                    "_id": str(kod),
                    "desc": movie_data.get("desc", "🎬 Kino"),
                    "episodes": [new_episode]
                })
        except Exception as e:
            logger.error(f"Kino MongoDB-ga saqlashda xato ({kod}): {e}")

async def delete_movie(kod):
    """Kinoni o'chiradi"""
    if "kinolar" in cached_data and str(kod) in cached_data["kinolar"]:
        del cached_data["kinolar"][str(kod)]
        await save_config()
    if movies_col is not None:
        try:
            await movies_col.delete_one({"_id": str(kod)})
        except Exception as e:
            logger.error(f"Kino o'chirishda xato ({kod}): {e}")

# ============= POST INIT =============
async def post_init(application: Application) -> None:
    global _notified_admin
    # MongoDB ulanishini tekshirish
    if db_client is not None:
        try:
            await db_client.server_info()
            logger.info("MongoDB ulanishi muvaffaqiyatli!")
        except Exception as e:
            logger.warning(f"MongoDB-ga ulanib bo'lmadi: {e}")
        
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
    cached_data.setdefault("foydalanuvchilar", {})[uid] = {
        "username": user.username or "",
        "first_name": user.first_name or "",
        "qoshilgan": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    if users_col is not None:
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
    await load_data()
    kanallar = cached_data.get("majburiy_kanallar", [])
    if not kanallar:
        return True
    for ch in kanallar:
        cid = str(ch.get("chat_id", "")).strip()
        if not cid:
            continue
        try:
            target_cid = int(cid) if (cid.startswith("-100") or cid.isdigit() or (cid.startswith("-") and cid[1:].isdigit())) else cid
            member = await bot.get_chat_member(chat_id=target_cid, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception as e:
            logger.warning(f"Obuna tekshirishda xato ({cid}): {e}")
            # Bot admin bo'lmagan yoki xato kanallar sababli foydalanuvchini bloklamaymiz
            continue
    return True

async def resolve_channel_info(context, message, raw_text=""):
    """Kanalni forward, @username, link yoki ID orqali aniqlaydi va bot adminligini tekshiradi"""
    chat_target = None
    if message.forward_from_chat:
        chat_target = message.forward_from_chat.id
    else:
        raw = raw_text.strip()
        if "t.me/" in raw:
            raw = raw.split("t.me/")[-1].replace("+", "").strip("/")
            if not raw.startswith("@") and not raw.startswith("-100"):
                raw = "@" + raw
        elif not raw.startswith("@") and not raw.startswith("-100") and not raw.isdigit():
            raw = "@" + raw
        chat_target = int(raw) if (raw.startswith("-100") or (raw.startswith("-") and raw[1:].isdigit())) else raw

    try:
        chat = await context.bot.get_chat(chat_target)
    except Exception as e:
        return None, f"Kanal topilmadi: {e}"

    try:
        member = await context.bot.get_chat_member(chat_id=chat.id, user_id=context.bot.id)
        if member.status not in ["administrator", "creator"]:
            return None, f"Bot <b>{html.escape(chat.title)}</b> kanalida ADMIN emas! Iltimos, oldin botga administrator huquqini bering."
    except Exception as e:
        return None, f"Kanalda admin huquqini tekshirib bo'lmadi: {e}"

    link = f"https://t.me/{chat.username}" if chat.username else ""
    if not link:
        try:
            invite = await context.bot.create_chat_invite_link(chat.id)
            link = invite.invite_link
        except Exception:
            link = BOT_URL

    return {
        "chat_id": str(chat.id),
        "title": chat.title,
        "username": chat.username or "",
        "url": link
    }, None

def sub_keyboard():
    rows = []
    for ch in cached_data.get("majburiy_kanallar", []):
        rows.append([InlineKeyboardButton(ch.get("name", "📢 Obuna bo'lish"), url=ch.get("url", BOT_URL))])
    rows.append([InlineKeyboardButton("✅ Obunani tasdiqlash", callback_data="check_sub")])
    return InlineKeyboardMarkup(rows)

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Kino qo'shish", callback_data="admin_add"),
         InlineKeyboardButton("🎞 Serial yuklash", callback_data="admin_add_serial")],
        [InlineKeyboardButton("📢 Kanalga post chiqarish", callback_data="admin_post_to_channel"),
         InlineKeyboardButton("📢 Rasmiy Kino Kanal", callback_data="admin_kino_channel")],
        [InlineKeyboardButton("✏️ Kinoni tahrirlash", callback_data="admin_edit_movie"),
         InlineKeyboardButton("🗑 Kino o'chirish", callback_data="admin_del")],
        [InlineKeyboardButton("📢 Majburiy kanal qo'shish", callback_data="admin_add_ch"),
         InlineKeyboardButton("❌ Majburiy kanal o'chirish", callback_data="admin_del_ch")],
        [InlineKeyboardButton("📣 Reklama tarqatish", callback_data="admin_broadcast"),
         InlineKeyboardButton("📊 Kodlar tahlili", callback_data="admin_code_analytics")],
        [InlineKeyboardButton("📚 Kinolar ro'yxati", callback_data="admin_list_movies"),
         InlineKeyboardButton("📈 To'liq statistika", callback_data="admin_stats")],
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
    if kod:
        if kod.startswith("kino_"):
            kod = kod[5:]
        elif kod.startswith("code_"):
            kod = kod[5:]
        kod = kod.strip()

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
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔥 Trend kinolar", callback_data="trend_movies"),
             InlineKeyboardButton("📚 Barcha kinolar", callback_data="list_movies")],
            [InlineKeyboardButton("❤️ Saqlangan kinolarim", callback_data="my_favorites"),
             InlineKeyboardButton("✍️ Kino buyurtma qilish", callback_data="order_movie")]
        ])
        await update.message.reply_text(
            f"🎬 <b>Assalomu alaykum {html.escape(user.first_name)}!</b>\n\n"
            "🔍 Kino kodini yoki nomini yozing — men darhol topib beraman!\n\n"
            "Quyidagi bo'limlardan birini tanlashingiz mumkin:",
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
        matches = []
        if movies_col is not None:
            try:
                import re as regex_lib
                safe_text = regex_lib.escape(kod)
                cursor = movies_col.find({"desc": {"$regex": safe_text, "$options": "i"}}).limit(10)
                matches = await cursor.to_list(length=10)
            except Exception as e:
                logger.error(f"Qidiruvda xato: {e}")
                matches = []
        if not matches:
            for k, m in cached_data.get("kinolar", {}).items():
                if kod.lower() in m.get("desc", "").lower():
                    matches.append(m)
                    if len(matches) >= 10:
                        break

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
                # Qism sonini ko'rsatish
                eps = v.get("episodes", [])
                ep_count = len(eps) if eps else 1
                suffix = f" ({ep_count} qism)" if ep_count > 1 else ""
                txt += f"▪️ <code>{v['_id']}</code> — {v.get('desc', '🎬')}{suffix}\n"
            txt += "\nKino kodini yuboring."
            await context.bot.send_message(chat_id=user_id, text=txt, parse_mode="HTML")
            return

    # Epizod tanlash yoki yuborish
    episodes = movie.get("episodes", [])
    # Migration: Agar episodes yo'q bo'lsa (eski format), yasab olamiz
    if not episodes and "msg_id" in movie:
        episodes = [{"msg_id": movie["msg_id"], "chat_id": movie.get("chat_id", STORAGE_CHANNEL_ID), "part": 1}]
    
    if not episodes:
        await context.bot.send_message(chat_id=user_id, text="⚠️ Ushbu koda video topilmadi.")
        return

    # Agar bir nechta qism bo'lsa va user qismni tanlamagan bo'lsa
    # (Misol uchun context.args da qism bormi ko'ramiz - kelajak uchun)
    if len(episodes) > 1:
        # Qismlar ro'yxatini chiqarish
        txt = f"🎬 <b>{html.escape(movie.get('desc', 'Kino'))}</b>\n\nUshbu koda {len(episodes)} ta qism topildi. Qaysi qismni ko'rmoqchisiz?"
        rows = []
        temp_row = []
        for ep in episodes:
            p = ep.get("part", 1)
            temp_row.append(InlineKeyboardButton(f"{p}-qism", callback_data=f"get_part_{kod}_{p}"))
            if len(temp_row) == 3:
                rows.append(temp_row)
                temp_row = []
        if temp_row: rows.append(temp_row)
        
        await context.bot.send_message(chat_id=user_id, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    # Faqat bitta qism bo'lsa (yoki birinchisini yuborish)
    await send_episode(update, context, movie, episodes[0])

async def send_episode(update: Update, context: ContextTypes.DEFAULT_TYPE, movie, episode):
    user_id = update.effective_user.id
    kod = movie["_id"]
    desc = movie.get("desc", "🎬 Kino")
    part = episode.get("part", 1)

    # Kino ma'lumotlarini qidirish
    details = await fetch_movie_details(desc)
    extra_info = ""
    if details:
        extra_info = f"\n📅 Yil: {details['year']} | ⭐ {details['rating']}"

    part_text = f" | {part}-qism" if part > 1 else ""
    
    # Ko'rishlar sonini tahlil uchun oshirish
    movie["views"] = movie.get("views", 0) + 1
    if "kinolar" in cached_data and str(kod) in cached_data["kinolar"]:
        cached_data["kinolar"][str(kod)]["views"] = movie["views"]
        await save_config()
    if movies_col is not None:
        try:
            await movies_col.update_one({"_id": str(kod)}, {"$inc": {"views": 1}})
        except: pass

    share_url = f"https://t.me/share/url?url={BOT_URL}?start={kod}&text=🎬 {html.escape(desc)}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Saqlash", callback_data=f"fav_{kod}"),
         InlineKeyboardButton("📤 Ulashish", url=share_url)]
    ])

    safe_desc = html.escape(desc)
    caption = f"🎬 <b>{safe_desc}</b>{part_text}{extra_info}\n🔑 Kod: <code>{kod}</code>\n\n👉 {BOT_URL}?start={kod}"

    status = await context.bot.send_message(chat_id=user_id, text="🔍 Yuklanmoqda...")
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=episode["chat_id"],
            message_id=episode["msg_id"],
            caption=caption,
            parse_mode="HTML",
            protect_content=True,
            reply_markup=kb
        )
        await status.delete()
        cached_data["statistika"]["jami_qidiruvlar"] = cached_data["statistika"].get("jami_qidiruvlar", 0) + 1
        try:
            await users_col.update_one({"_id": str(user_id)}, {"$inc": {"qidiruvlar": 1}})
        except: pass
        await save_config()
    except Exception as e:
        logger.error(f"Episode yuborishda xato: {e}")
        await status.edit_text("❌ Xatolik yuz berdi. Video o'chirilgan bo'lishi mumkin.")

# ============= MATN HANDLER =============
async def send_promo_to_channel(context, admin_user, kod, text_or_caption="", photo=None, video=None, animation=None):
    movie = await get_movie(kod)
    m_desc = movie.get("desc", "🎬 Yangi Premyera") if movie else "🎬 Yangi Premyera"
    
    caption_body = text_or_caption.strip() if text_or_caption and text_or_caption.lower() != "avto" else ""
    if caption_body:
        post_text = (
            f"🎬 <b>{html.escape(m_desc)}</b>\n\n"
            f"{html.escape(caption_body)}\n\n"
            f"🔢 <b>Kino kodi:</b> <code>{kod}</code>\n\n"
            f"🍿 <b>Kinoni to'liq holda bot orqali tomosha qiling:</b>\n"
            f"👇 Quyidagi tugmani bosing!"
        )
    else:
        post_text = (
            f"🎬 <b>{html.escape(m_desc)}</b>\n\n"
            f"🔢 <b>Kino kodi:</b> <code>{kod}</code>\n\n"
            f"🍿 <b>Kinoni to'liq holda bot orqali tomosha qiling:</b>\n"
            f"👇 Quyidagi tugmani bosing!"
        )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Kinoni ko'rish", url=f"{BOT_URL}?start={kod}")]
    ])
    
    k_ch = cached_data.get("kino_kanal", {})
    target_chat = k_ch.get("chat_id")
    
    success = False
    if target_chat:
        try:
            t_id = int(target_chat) if (target_chat.startswith("-100") or target_chat.isdigit() or (target_chat.startswith("-") and target_chat[1:].isdigit())) else target_chat
            if video:
                await context.bot.send_video(chat_id=t_id, video=video, caption=post_text, parse_mode="HTML", reply_markup=kb)
            elif photo:
                await context.bot.send_photo(chat_id=t_id, photo=photo, caption=post_text, parse_mode="HTML", reply_markup=kb)
            elif animation:
                await context.bot.send_animation(chat_id=t_id, animation=animation, caption=post_text, parse_mode="HTML", reply_markup=kb)
            else:
                await context.bot.send_message(chat_id=t_id, text=post_text, parse_mode="HTML", reply_markup=kb)
            success = True
        except Exception as e:
            logger.error(f"Kanalga post yuborishda xato: {e}")
            await context.bot.send_message(chat_id=admin_user.id, text=f"⚠️ Kanalga yuborishda xatolik: {e}")
    
    if success:
        c_title = k_ch.get("title", "Kino Kanali")
        await context.bot.send_message(
            chat_id=admin_user.id,
            text=f"✅ Reklama posti <b>{html.escape(c_title)}</b> kanaliga muvaffaqiyatli joylandi! 🚀",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
    else:
        await context.bot.send_message(
            chat_id=admin_user.id,
            text=(
                f"ℹ️ <b>Tayyor post namunasi:</b>\n"
                f"<i>(Rasmiy kanal ulanmaganligi uchun post sizga yuborildi. Uni kanalingizga forward qilishingiz mumkin yoki Admin paneldan '📢 Rasmiy Kino Kanal' ni ulang)</i>"
            ),
            parse_mode="HTML"
        )
        if video:
            await context.bot.send_video(chat_id=admin_user.id, video=video, caption=post_text, parse_mode="HTML", reply_markup=kb)
        elif photo:
            await context.bot.send_photo(chat_id=admin_user.id, photo=photo, caption=post_text, parse_mode="HTML", reply_markup=kb)
        else:
            await context.bot.send_message(chat_id=admin_user.id, text=post_text, parse_mode="HTML", reply_markup=kb)
        await context.bot.send_message(chat_id=admin_user.id, text="👑 Admin Panel:", reply_markup=admin_keyboard())

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

        elif state == "WAIT_CH_INPUT":
            ch_data, err = await resolve_channel_info(context, update.message, text)
            if err:
                await update.message.reply_text(f"❌ {err}\n\nIltimos, qayta urinib ko'ring yoki /cancel deb yozing:", parse_mode="HTML")
                return
            cached_data.setdefault("majburiy_kanallar", []).append({
                "chat_id": ch_data["chat_id"],
                "url": ch_data["url"],
                "name": ch_data["title"]
            })
            await save_config()
            global last_config_load
            last_config_load = 0
            await set_state(user.id, None)
            await update.message.reply_text(
                f"✅ <b>{html.escape(ch_data['title'])}</b> kanali muvaffaqiyatli majburiy obunaga qo'shildi!\n"
                f"Bot adminligi tasdiqlandi. 🚀",
                parse_mode="HTML",
                reply_markup=admin_keyboard()
            )
            return

        elif state == "WAIT_KINO_CH_INPUT":
            ch_data, err = await resolve_channel_info(context, update.message, text)
            if err:
                await update.message.reply_text(f"❌ {err}\n\nIltimos, qayta urinib ko'ring yoki /cancel deb yozing:", parse_mode="HTML")
                return
            cached_data["kino_kanal"] = ch_data
            await save_config()
            await set_state(user.id, None)
            await update.message.reply_text(
                f"✅ <b>{html.escape(ch_data['title'])}</b> rasmiy kino kanali sifatida ulandi!\n\n"
                f"Endi har bir kinoni reklama posti va <code>[Kinoni ko'rish]</code> tugmasi bilan kanalga yuborishingiz mumkin.",
                parse_mode="HTML",
                reply_markup=admin_keyboard()
            )
            return

        elif state == "WAIT_POST_CODE":
            p_kod = text.strip()
            movie = await get_movie(p_kod)
            if not movie:
                await update.message.reply_text(f"❌ <code>{p_kod}</code> kodi bo'yicha kino topilmadi. Qayta kiriting:", parse_mode="HTML")
                return
            await set_state(user.id, "WAIT_POST_MEDIA", {"kod": p_kod})
            await update.message.reply_text(
                f"📢 <b>{p_kod} kodli kino uchun kanalga reklama posti:</b>\n\n"
                f"Quyidagilardan birini yuboring:\n"
                f"📹 <b>Qisqa treyler / video</b> (izohi bilan birga)\n"
                f"🖼 <b>Poster / rasm</b> (izohi bilan birga)\n"
                f"📝 <b>Reklama matni</b>\n"
                f"⚡ Yoki shunchaki <b>Avto</b> deb yozing\n\n"
                f"<i>(Bekor qilish uchun /cancel)</i>",
                parse_mode="HTML"
            )
            return

        elif state == "WAIT_POST_MEDIA":
            p_kod = sdata.get("kod")
            await set_state(user.id, None)
            await send_promo_to_channel(context, user, p_kod, text_or_caption=text)
            return

        elif state == "WAIT_MOVIE_CODE":
            # Admin kino kodini kiritdi
            m_parts = text.split()
            m_kod = m_parts[0].replace("#", "").strip()
            m_part = 1
            p_match = re.search(r'(\d+)\s*(?:-?\s*qism|seriya|part|ep)', text, re.IGNORECASE)
            if p_match:
                m_part = int(p_match.group(1))

            await set_state(user.id, "WAIT_MOVIE_DESC", {
                **sdata,
                "kod": m_kod,
                "part": m_part
            })
            await update.message.reply_text(
                f"✅ <b>Kod:</b> <code>{m_kod}</code> qabul qilindi!\n\n"
                f"2️⃣ Endi kino <b>nomi va tavsifi</b>ni kiriting (masalan: <i>Forsaj 10 (2023) Uzbek tilida</i>):\n\n"
                f"<i>(Agar tavsif kiritishni istamasangiz, <code>/skip</code> deb yuboring)</i>",
                parse_mode="HTML"
            )
            return

        elif state == "WAIT_MOVIE_DESC":
            m_kod = sdata.get("kod")
            m_part = sdata.get("part", 1)
            msg_id = sdata.get("msg_id")
            chat_id = sdata.get("chat_id")
            desc = text if text != "/skip" else "🎬 Kino"

            status = await update.message.reply_text(f"📥 `{m_kod}` kodi bilan saqlanmoqda...", parse_mode="Markdown")
            try:
                await complete_movie_save(context, user.id, msg_id, chat_id, m_kod, desc, m_part)
                await set_state(user.id, None)
                await status.delete()
                kb_promo = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Kanalga reklama posti chiqarish", callback_data=f"post_kino_{m_kod}")],
                    [InlineKeyboardButton("👑 Admin Panel", callback_data="admin_back")]
                ])
                await update.message.reply_text(
                    f"✅ <b>{('Serial' if m_part > 1 else 'Kino')} muvaffaqiyatli saqlandi!</b>\n\n"
                    f"🔑 Kod: <code>{m_kod}</code>\n"
                    f"🔢 Qism: {m_part}\n"
                    f"📝 Nomi: {html.escape(desc)}\n\n"
                    f"📢 Ushbu kino uchun kanalga reklama posti chiqarasizmi?",
                    parse_mode="HTML",
                    reply_markup=kb_promo
                )
            except Exception as e:
                logger.error(f"Kino qadam-baqadam saqlashda xato: {e}")
                await status.edit_text(f"❌ Xatolik yuz berdi: {e}")
            return

        elif state == "WAIT_EDIT_CODE":
            movie = await get_movie(text)
            if not movie:
                await update.message.reply_text(f"❌ <code>{text}</code> kodli kino topilmadi.", parse_mode="HTML", reply_markup=admin_keyboard())
                await set_state(user.id, None)
                return
            await set_state(user.id, "WAIT_EDIT_DESC", {"kod": text})
            await update.message.reply_text(
                f"🎬 <b>Kino topildi!</b>\n\n"
                f"🔑 Kod: <code>{text}</code>\n"
                f"📝 Hozirgi nomi: <b>{html.escape(movie.get('desc', '🎬'))}</b>\n\n"
                f"Yangi nom va tavsifni yuboring:",
                parse_mode="HTML"
            )
            return

        elif state == "WAIT_EDIT_DESC":
            e_kod = sdata.get("kod")
            new_desc = text.strip()
            movie = await get_movie(e_kod)
            if movie:
                movie["desc"] = new_desc
                if "kinolar" in cached_data and str(e_kod) in cached_data["kinolar"]:
                    cached_data["kinolar"][str(e_kod)]["desc"] = new_desc
                    await save_config()
                if movies_col is not None:
                    try:
                        await movies_col.update_one({"_id": str(e_kod)}, {"$set": {"desc": new_desc}})
                    except: pass
                await update.message.reply_text(
                    f"✅ <code>{e_kod}</code> kodli kino nomi yangilandi:\n<b>{html.escape(new_desc)}</b>",
                    parse_mode="HTML",
                    reply_markup=admin_keyboard()
                )
            else:
                await update.message.reply_text("❌ Xatolik yuz berdi.", reply_markup=admin_keyboard())
            await set_state(user.id, None)
            return

        elif state == "WAIT_AD_CONTENT" or state == "WAIT_AD_TEXT":
            await do_broadcast(update, context)
            return

    # ----- FOYDALANUVCHI: BUYURTMA VA HOLATLAR -----
    if state == "WAIT_ORDER_TEXT":
        await set_state(user.id, None)
        order_text = text.strip()
        # Adminlarga yuborish
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=aid,
                    text=(
                        f"📩 <b>Yangi kino buyurtmasi!</b>\n\n"
                        f"👤 <b>Foydalanuvchi:</b> <a href=\"tg://user?id={user.id}\">{html.escape(user.first_name)}</a> (<code>{user.id}</code>)\n"
                        f"🎬 <b>So'ralgan kino:</b> <code>{html.escape(order_text)}</code>"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Admin {aid} ga buyurtma yuborilmadi: {e}")
        await update.message.reply_text(
            "✅ <b>Kino buyurtmangiz qabul qilindi!</b>\n\n"
            "Adminlarimiz tez orada ushbu kinoni botga yuklashadi. Rahmat!",
            parse_mode="HTML"
        )
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

# ============= BROADCAST FUNKSIYASI (MATN, RASM, VIDEO) =============
async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    _, sdata = await get_state(admin_id)
    target = sdata.get("target", "all") if sdata else "all"

    status_msg = await update.message.reply_text("📣 Reklama tarqatish boshlandi...")

    ok_ch = ok_usr = fail_ch = fail_usr = 0

    # 1. Kanallar va guruhlarga
    if target in ["channels", "all"]:
        channel_targets = set()
        for cid in list(cached_data.get("guruhlar", [])):
            channel_targets.add(str(cid))
        k_ch = cached_data.get("kino_kanal", {})
        if k_ch.get("chat_id"):
            channel_targets.add(str(k_ch.get("chat_id")))
        for ch in cached_data.get("majburiy_kanallar", []):
            if ch.get("chat_id"):
                channel_targets.add(str(ch.get("chat_id")))

        for cid in channel_targets:
            try:
                target_id = int(cid) if (cid.startswith("-100") or cid.isdigit() or (cid.startswith("-") and cid[1:].isdigit())) else cid
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=update.message.chat_id,
                    message_id=update.message.message_id
                )
                ok_ch += 1
                await asyncio.sleep(0.3)
            except Exception:
                fail_ch += 1

    # 2. Foydalanuvchilarga
    if target in ["users", "all"]:
        users_to_send = set()
        for uid in cached_data.get("foydalanuvchilar", {}).keys():
            users_to_send.add(str(uid))

        if users_col is not None:
            try:
                cursor = users_col.find({}, {"_id": 1})
                async for user_doc in cursor:
                    users_to_send.add(str(user_doc["_id"]))
            except Exception as e:
                logger.error(f"Broadcast user list error: {e}")

        for uid in users_to_send:
            try:
                await context.bot.copy_message(
                    chat_id=int(uid),
                    from_chat_id=update.message.chat_id,
                    message_id=update.message.message_id
                )
                ok_usr += 1
                await asyncio.sleep(0.05)
            except Exception:
                fail_usr += 1

    t_title = "👤 Foydalanuvchilarga" if target == "users" else ("📢 Kanal va guruhlarga" if target == "channels" else "🚀 Barchaga")
    res_text = (
        f"📣 <b>Reklama muvaffaqiyatli yakunlandi!</b>\n\n"
        f"🎯 Auditoriya: <b>{t_title}</b>\n"
    )
    if target in ["users", "all"]:
        res_text += f"👥 Foydalanuvchilar: <b>{ok_usr}</b> ta (Xato: {fail_usr})\n"
    if target in ["channels", "all"]:
        res_text += f"📢 Kanallar va guruhlar: <b>{ok_ch}</b> ta (Xato: {fail_ch})"

    await status_msg.edit_text(res_text, parse_mode="HTML")
    await set_state(admin_id, None)
    await update.message.reply_text("👑 Admin Panel:", reply_markup=admin_keyboard())

# ============= KINO SAQLASH YORDAMCHISI =============
async def complete_movie_save(context, admin_chat_id, msg_id, from_chat_id, kod, desc, part=1):
    if STORAGE_CHANNEL_ID:
        try:
            sent = await context.bot.copy_message(
                chat_id=STORAGE_CHANNEL_ID,
                from_chat_id=from_chat_id,
                message_id=msg_id
            )
            store_chat = STORAGE_CHANNEL_ID
            store_msg = sent.message_id
        except Exception as e:
            logger.warning(f"Storage kanalga nusxalashda xato: {e}")
            store_chat = from_chat_id
            store_msg = msg_id
    else:
        store_chat = from_chat_id
        store_msg = msg_id

    movie_data = {
        "msg_id": store_msg,
        "chat_id": store_chat,
        "desc": desc,
        "part": part,
        "views": 0
    }
    await save_movie(kod, movie_data)
    await broadcast_new_movie(context, kod, desc)

# ============= MEDIA HANDLER (ADMIN KINO YUKLASH) =============
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if user.id not in ADMIN_IDS:
        return

    if chat.type != "private":
        return

    state, sdata = await get_state(user.id)
    if state == "WAIT_CH_INPUT":
        ch_data, err = await resolve_channel_info(context, update.message, "")
        if err:
            await update.message.reply_text(f"❌ {err}\n\nIltimos, qayta urinib ko'ring yoki /cancel deb yozing:", parse_mode="HTML")
            return
        cached_data.setdefault("majburiy_kanallar", []).append({
            "chat_id": ch_data["chat_id"],
            "url": ch_data["url"],
            "name": ch_data["title"]
        })
        await save_config()
        global last_config_load
        last_config_load = 0
        await set_state(user.id, None)
        await update.message.reply_text(f"✅ <b>{html.escape(ch_data['title'])}</b> kanali majburiy obunaga qo'shildi!", parse_mode="HTML", reply_markup=admin_keyboard())
        return

    if state == "WAIT_KINO_CH_INPUT":
        ch_data, err = await resolve_channel_info(context, update.message, "")
        if err:
            await update.message.reply_text(f"❌ {err}\n\nIltimos, qayta urinib ko'ring yoki /cancel deb yozing:", parse_mode="HTML")
            return
        cached_data["kino_kanal"] = ch_data
        await save_config()
        await set_state(user.id, None)
        await update.message.reply_text(f"✅ <b>{html.escape(ch_data['title'])}</b> rasmiy kino kanali sifatida ulandi!", parse_mode="HTML", reply_markup=admin_keyboard())
        return

    if state == "WAIT_POST_MEDIA":
        p_kod = sdata.get("kod")
        caption = update.message.caption or ""
        v = update.message.video.file_id if update.message.video else None
        p = update.message.photo[-1].file_id if update.message.photo else None
        a = update.message.animation.file_id if update.message.animation else None
        await set_state(user.id, None)
        await send_promo_to_channel(context, user, p_kod, text_or_caption=caption, photo=p, video=v, animation=a)
        return

    # Agar admin reklama yuborayotgan bo'lsa (rasm, video yoki xabar)
    if state == "WAIT_AD_CONTENT":
        await do_broadcast(update, context)
        return

    caption = update.message.caption or ""

    # Agar videoga hech qanday izoh yozilmagan bo'lsa -> QADAM-BAQADAM SO'RAYMIZ!
    if not caption.strip():
        await set_state(user.id, "WAIT_MOVIE_CODE", {
            "msg_id": update.message.message_id,
            "chat_id": update.message.chat_id
        })
        await update.message.reply_text(
            "🎬 <b>Video qabul qilindi!</b>\n\n"
            "1️⃣ Ushbu kino uchun <b>kod</b> kiriting (masalan: <code>125</code>):\n\n"
            "<i>(Agar serial bo'lsa, qism bilan yozishingiz ham mumkin: <code>125 2-qism</code>)</i>",
            parse_mode="HTML"
        )
        return

    # Agar birdaniga izoh bilan yuborilgan bo'lsa (eski qulay format)
    kod = None
    desc = caption.strip()

    m = re.search(r'(?i)\b(?:id|kodi|kod)\b\s*[:\-]?\s*([A-Za-z0-9]+)', caption)
    if m:
        kod = m.group(1)
        desc = re.sub(re.escape(m.group(0)), '', caption, flags=re.IGNORECASE).strip()
    
    if not kod:
        m = re.search(r'(?i)[#№]\s*([A-Za-z0-9]+)', caption)
        if m:
            kod = m.group(1)
            desc = re.sub(re.escape(m.group(0)), '', caption, flags=re.IGNORECASE).strip()

    part = None
    p_match = re.search(r'(\d+)\s*(?:-?\s*qism|seriya|qisimi|part|ep|episode)', caption, re.IGNORECASE)
    if p_match:
        part = int(p_match.group(1))

    if not kod:
        session = cached_data.get("admin_sessions", {}).get(str(user.id))
        if session:
            kod = session.get("kod")
            desc = session.get("desc", "🎬 Serial")
            if part is None:
                part = session.get("last_part", 1) + 1

    if not kod:
        await set_state(user.id, "WAIT_MOVIE_CODE", {
            "msg_id": update.message.message_id,
            "chat_id": update.message.chat_id
        })
        await update.message.reply_text(
            "🎬 <b>Video qabul qilindi!</b>\n\n"
            "1️⃣ Ushbu kino uchun <b>kod</b> kiriting (masalan: <code>125</code>):",
            parse_mode="HTML"
        )
        return

    if part is None:
        part = 1

    if "admin_sessions" not in cached_data: cached_data["admin_sessions"] = {}
    cached_data["admin_sessions"][str(user.id)] = {
        "kod": kod,
        "desc": desc,
        "last_part": part,
        "time": datetime.now()
    }

    if not desc or desc == caption:
        desc = "🎬 Kino"

    status = await update.message.reply_text(f"📥 Kino `{kod}` kodi bilan saqlanmoqda...", parse_mode="Markdown")
    try:
        await complete_movie_save(context, user.id, update.message.message_id, update.message.chat_id, kod, desc, part)
        await status.delete()
        await update.message.reply_text(
            f"✅ <b>{('Serial' if part > 1 else 'Kino')} saqlandi!</b>\n\n"
            f"🔑 Kod: <code>{kod}</code>\n"
            f"🔢 Qism: {part}\n"
            f"📝 Nomi: {html.escape(desc)}",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
    except Exception as e:
        logger.error(f"Kino saqlashda xato: {e}")
        await status.edit_text(f"❌ Xatolik: {e}")

# ============= YANGI KINO REKLAMASI =============
async def broadcast_new_movie(context, kod, desc):
    import html
    safe_desc = html.escape(desc)
    msg = (
        f"🎬 <b>YANGI PREMYERA!</b>\n\n"
        f"📝 <b>Tavsif:</b> {safe_desc}\n"
        f"🔑 <b>Kino kodi:</b> <code>{kod}</code>\n\n"
        f"🍿 <i>Kinoni tomosha qilish uchun pastdagi tugmani bosing:</i>"
    )
    # Botga to'g'ridan-to'g'ri olib o'tuvchi inline tugma
    watch_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Kinoni tomosha qilish", url=f"{BOT_URL}?start={kod}")]
    ])
    
    # 1. Barcha ulangan kanallar va guruhlar
    targets = set()
    for cid in cached_data.get("guruhlar", []):
        targets.add(str(cid))
    for ch in cached_data.get("majburiy_kanallar", []):
        cid = ch.get("chat_id")
        if cid:
            targets.add(str(cid))
            
    for cid in targets:
        try:
            await context.bot.send_message(
                chat_id=cid,
                text=msg,
                parse_mode="HTML",
                reply_markup=watch_kb
            )
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"Kanal/guruhga reklama yuborishda xato ({cid}): {e}")

# ============= CALLBACK HANDLER =============
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    cb = q.data

    if cb.startswith("get_part_"):
        # get_part_KOD_PART
        parts = cb.split("_")
        kod = parts[2]
        p_num = int(parts[3])
        movie = await get_movie(kod)
        if movie:
            episodes = movie.get("episodes", [])
            target = next((e for e in episodes if e.get("part") == p_num), None)
            if target:
                await send_episode(update, context, movie, target)
            else:
                await q.message.reply_text("❌ Ushbu qism topilmadi.")
        return

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
        else:
            await q.message.reply_text("❌ Hali barcha kanallarga obuna bo'lmadingiz! Iltimos, yuqoridagi kanallarga a'zo bo'ling.")
        return

    # Foydalanuvchilar va umumiy tugmalar
    if cb == "trend_movies":
        # Ko'rishlar bo'yicha eng ko'p ko'rilgan 10 ta kino
        all_m = list(cached_data.get("kinolar", {}).values())
        all_m.sort(key=lambda x: x.get("views", 0), reverse=True)
        top_10 = all_m[:10]
        if not top_10:
            await q.message.reply_text("Hozircha trend kinolar yo'q.")
            return
        txt = "🔥 <b>Eng ommabop va ko'p ko'rilgan kinolar:</b>\n\n"
        for i, m in enumerate(top_10, 1):
            k_id = m.get('_id', m.get('kod', ''))
            views = m.get('views', 0)
            txt += f"{i}. <code>{k_id}</code> — <b>{html.escape(m.get('desc', '🎬'))}</b> ({views} marta ko'rilgan)\n"
        txt += "\nKinoni tomosha qilish uchun uning kodini botga yuboring!"
        await q.message.reply_text(txt, parse_mode="HTML")
        return

    elif cb == "my_favorites":
        fav_list = cached_data.get("favorites", {}).get(str(uid), [])
        if not fav_list:
            await q.message.reply_text(
                "❤️ <b>Sizda hali saqlangan kinolar yo'q.</b>\n\n"
                "Kinolarni ko'rayotganda ostidagi '❤️ Saqlash' tugmasini bossangiz, shu yerda saqlanadi!",
                parse_mode="HTML"
            )
            return
        txt = "❤️ <b>Siz saqlagan kinolar ro'yxati:</b>\n\n"
        for f_kod in fav_list:
            m = await get_movie(f_kod)
            name = m.get('desc', '🎬 Kino') if m else 'Kino'
            txt += f"▪️ <code>{f_kod}</code> — <b>{html.escape(name)}</b>\n"
        txt += "\nKo'rmoqchi bo'lgan kino kodini yuboring."
        await q.message.reply_text(txt, parse_mode="HTML")
        return

    elif cb.startswith("fav_"):
        f_kod = cb.split("_")[1]
        if "favorites" not in cached_data:
            cached_data["favorites"] = {}
        u_favs = cached_data["favorites"].setdefault(str(uid), [])
        if f_kod in u_favs:
            u_favs.remove(f_kod)
            await save_config()
            await q.answer("💔 Kino sevimlilardan o'chirildi", show_alert=True)
        else:
            u_favs.append(f_kod)
            await save_config()
            await q.answer("❤️ Kino sevimlilarga saqlandi!", show_alert=True)
        return

    elif cb == "order_movie":
        await set_state(uid, "WAIT_ORDER_TEXT")
        await q.message.reply_text(
            "✍️ <b>Kino buyurtma qilish:</b>\n\n"
            "Qaysi kinoni topa olmadingiz? Nomini (va iloji bo'lsa yilini) yozib yuboring:\n"
            "<i>(Masalan: O'rgimchak odam 3 (2007))</i>",
            parse_mode="HTML"
        )
        return

    # Admin tugmalari
    if uid not in ADMIN_IDS:
        return

    if cb == "admin_code_analytics":
        all_m = list(cached_data.get("kinolar", {}).values())
        if not all_m:
            await q.message.reply_text("Kinolar mavjud emas.")
            return
        all_m.sort(key=lambda x: x.get("views", 0), reverse=True)
        total_views = sum(m.get("views", 0) for m in all_m)
        txt = (
            f"📊 <b>Kodlar tahlili va ko'rishlar statistikasi:</b>\n\n"
            f"🎬 Jami yuklangan kinolar: <b>{len(all_m)}</b> ta\n"
            f"👁 Jami umumiy ko'rishlar: <b>{total_views}</b> marta\n\n"
            f"🏆 <b>Eng ommabop 15 ta kod:</b>\n"
        )
        for i, m in enumerate(all_m[:15], 1):
            k_id = m.get('_id', m.get('kod', ''))
            views = m.get('views', 0)
            txt += f"{i}. <code>{k_id}</code> — <b>{views}</b> ko'rish ({html.escape(m.get('desc', '🎬'))[:25]})\n"
        await q.message.reply_text(txt, parse_mode="HTML", reply_markup=admin_keyboard())
        return

    elif cb == "admin_edit_movie":
        await set_state(uid, "WAIT_EDIT_CODE")
        await q.message.reply_text("✏️ Tahrirlamoqchi bo'lgan kino *kodini* yuboring:", parse_mode="Markdown")
        return

    if cb == "admin_add":
        await q.message.reply_text(
            "🎬 *Kino qo'shish:*\n\n"
            "Videoni yuboring va podpisiga quyidagi formatda yozing:\n"
            "`Kod: 123 Nom` yoki `#123 Nom`",
            parse_mode="Markdown"
        )

    elif cb == "admin_add_serial":
        await q.message.reply_text(
            "🎞 *Serial yuklash (Auto-Serial mode):*\n\n"
            "1. Birinchi qismni odatdagidek yuboring: `#100 Serial Nom Qism: 1`.\n"
            "2. Keyingi qismlarni (video) shunchaki **kod yozmasdan** yuboravering.\n"
            "3. Bot ularni avtomatik ravishda `#100` kodiga 2, 3, 4-qismlar qilib qo'shadi.\n\n"
            "💡 *Eslatma:* Har doim botning shaxsiyiga yuboring (guruhga emas).",
            parse_mode="Markdown"
        )

    elif cb == "admin_del":
        await set_state(uid, "WAIT_DEL_CODE")
        await q.message.reply_text("🗑 O'chirmoqchi bo'lgan kino *kodini* yuboring:", parse_mode="Markdown")

    elif cb == "admin_add_ch":
        await set_state(uid, "WAIT_CH_INPUT")
        await q.message.reply_text(
            "📢 <b>Majburiy obuna uchun kanal qo'shish:</b>\n\n"
            "Kanalning <b>@username</b>ini kiriting yoki kanaldan bitta xabarni bu yerga <b>Forward</b> qiling:\n\n"
            "<i>⚠️ Muhim: @kino_liive_bot kanalda Administrator bo'lishi shart!</i>",
            parse_mode="HTML"
        )

    elif cb == "admin_del_ch":
        kanallar = cached_data.get("majburiy_kanallar", [])
        if not kanallar:
            await q.message.reply_text("Hozircha majburiy kanal yo'q.", reply_markup=admin_keyboard())
            return
        rows = [[InlineKeyboardButton(f"❌ {ch['name']}", callback_data=f"del_ch_{i}")] for i, ch in enumerate(kanallar)]
        rows.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])
        await q.message.reply_text("Qaysi kanalni o'chirasiz?", reply_markup=InlineKeyboardMarkup(rows))

    elif cb == "admin_kino_channel":
        k_ch = cached_data.get("kino_kanal", {})
        c_title = k_ch.get("title", "Ulanmagan")
        c_user = f"@{k_ch.get('username')}" if k_ch.get("username") else k_ch.get("chat_id", "Yo'q")
        txt = (
            f"📢 <b>Rasmiy Kino Kanal:</b>\n\n"
            f"🎬 Ulangan kanal: <b>{html.escape(c_title)}</b> ({c_user})\n\n"
            f"Ushbu kanalga kinolar reklama posti (treyler) va <code>[🎬 Kinoni ko'rish]</code> tugmasi bilan avtomatik joylanishi mumkin."
        )
        btns = [
            [InlineKeyboardButton("➕ Yangi kanal ulash", callback_data="admin_set_kino_channel")],
        ]
        if k_ch.get("chat_id"):
            btns.append([InlineKeyboardButton("🗑 Kanalni uzish", callback_data="admin_del_kino_channel")])
        btns.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])
        await q.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))

    elif cb == "admin_set_kino_channel":
        await set_state(uid, "WAIT_KINO_CH_INPUT")
        await q.message.reply_text(
            "📢 <b>Rasmiy Kino Kanalini ulash:</b>\n\n"
            "Kanal <b>@username</b>ini kiriting yoki kanaldan istalgan bir postni bu yerga <b>Forward</b> qiling:\n\n"
            "<i>⚠️ Muhim: @kino_liive_bot kanalda Administrator bo'lishi shart!</i>",
            parse_mode="HTML"
        )

    elif cb == "admin_del_kino_channel":
        cached_data["kino_kanal"] = {}
        await save_config()
        await q.message.reply_text("✅ Rasmiy kino kanali uzildi.", reply_markup=admin_keyboard())

    elif cb == "admin_post_to_channel":
        await set_state(uid, "WAIT_POST_CODE")
        await q.message.reply_text(
            "📢 <b>Kanalga post chiqarish:</b>\n\n"
            "Qaysi kino uchun post chiqarmoqchisiz? Kino <b>kodini</b> kiriting (masalan: <code>284</code>):",
            parse_mode="HTML"
        )

    elif cb.startswith("post_kino_"):
        p_kod = cb.replace("post_kino_", "").strip()
        movie = await get_movie(p_kod)
        if not movie:
            await q.message.reply_text("❌ Kino topilmadi.", reply_markup=admin_keyboard())
            return
        await set_state(uid, "WAIT_POST_MEDIA", {"kod": p_kod})
        await q.message.reply_text(
            f"📢 <b>{p_kod} kodli kino uchun kanalga reklama posti:</b>\n\n"
            f"Quyidagilardan birini yuboring:\n"
            f"📹 <b>Qisqa treyler / video</b> (izohi bilan birga)\n"
            f"🖼 <b>Poster / rasm</b> (izohi bilan birga)\n"
            f"📝 <b>Reklama matni</b>\n"
            f"⚡ Yoki shunchaki <b>Avto</b> deb yuboring\n\n"
            f"<i>(Bekor qilish uchun /cancel)</i>",
            parse_mode="HTML"
        )

    elif cb == "admin_broadcast":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Faqat bot foydalanuvchilariga", callback_data="ad_target_users")],
            [InlineKeyboardButton("📢 Ulangan kanal va guruhlarga", callback_data="ad_target_channels")],
            [InlineKeyboardButton("🚀 Barchaga (Foydalanuvchilar + Kanallar)", callback_data="ad_target_all")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")]
        ])
        await q.message.reply_text(
            "📣 <b>Reklama tarqatish:</b>\n\n"
            "Reklamani qayerga yubormoqchisiz? Tanlang:",
            parse_mode="HTML",
            reply_markup=kb
        )

    elif cb in ["ad_target_users", "ad_target_channels", "ad_target_all"]:
        target = "users" if cb == "ad_target_users" else ("channels" if cb == "ad_target_channels" else "all")
        t_name = "👤 Faqat bot foydalanuvchilariga" if target == "users" else ("📢 Kanal va guruhlarga" if target == "channels" else "🚀 Barchaga")
        await set_state(uid, "WAIT_AD_CONTENT", {"target": target})
        await q.message.reply_text(
            f"🎯 <b>Tanlangan auditoriya:</b> {t_name}\n\n"
            f"Endi tarqatmoqchi bo'lgan reklama xabaringizni yuboring (matn, rasm yoki video):\n\n"
            f"<i>(Bekor qilish uchun /cancel)</i>",
            parse_mode="HTML"
        )

    elif cb == "admin_stats":
        kinolar = len(cached_data.get("kinolar", {}))
        users = len(cached_data.get("foydalanuvchilar", {}))
        if movies_col is not None:
            try:
                kinolar = await movies_col.count_documents({})
                users = await users_col.count_documents({})
            except: pass
            
        groups = len(cached_data.get("guruhlar", []))
        searches = cached_data.get("statistika", {}).get("jami_qidiruvlar", 0)
        channels = len(cached_data.get("majburiy_kanallar", []))
        await q.edit_message_text(
            f"📊 <b>Statistika:</b>\n\n"
            f"🎬 Kinolar: {kinolar} ta\n"
            f"👤 Foydalanuvchilar: {users} ta\n"
            f"🏢 Guruhlar: {groups} ta\n"
            f"📢 Majburiy kanallar: {channels} ta\n"
            f"🔍 Jami qidiruvlar: {searches} ta",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )

    elif cb == "admin_list_movies" or cb == "list_movies":
        movies = []
        if movies_col is not None:
            try:
                cursor = movies_col.find().sort("_id", -1).limit(30)
                movies = await cursor.to_list(length=30)
            except Exception as e:
                logger.error(f"List movies error: {e}")
        if not movies:
            all_m = list(cached_data.get("kinolar", {}).values())
            movies = all_m[-30:]
        if not movies:
            await q.message.reply_text("Kinolar mavjud emas.")
            return
        txt = "🎬 <b>Oxirgi qo'shilgan kinolar:</b>\n\n"
        for m in movies:
            txt += f"▪️ <code>{m.get('_id', m.get('kod', ''))}</code> — {html.escape(m.get('desc', '🎬'))}\n"
        
        if uid in ADMIN_IDS:
            await q.edit_message_text(txt, parse_mode="HTML", reply_markup=admin_keyboard())
        else:
            await q.message.reply_text(txt, parse_mode="HTML")

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
    await app.updater.start_polling(drop_pending_updates=True)

    # Railway / Hosting PORT tekshiruvi (Health-check)
    port_str = os.environ.get("PORT")
    if port_str:
        try:
            port = int(port_str)
            from aiohttp import web
            async def handle_ping(request):
                return web.Response(text="Bot is running! @kino_liive_bot")
            server = web.Application()
            server.router.add_get("/", handle_ping)
            server.router.add_get("/health", handle_ping)
            runner = web.AppRunner(server)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            logger.info(f"Health-check web server {port}-portda ishga tushdi.")
        except Exception as e:
            logger.warning(f"Health-check serverni yoqishda xatolik: {e}")

    logger.info("Bot muvaffaqiyatli ishga tushdi va xabarlarni kutmoqda.")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
