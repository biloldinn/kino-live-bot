import json
import os
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, 
    CallbackQueryHandler, ChatMemberHandler
)

import httpx
import re

_notified_admin = False

# ============= SOZLAMALAR =============
# Bot tokeningiz
TOKEN = os.environ.get("BOT_TOKEN", "8679177935:AAHd2tcTrf_P0F7396UJjJXNjVjNkxL6lw0")  

# Admin IDsi (Sizniki kiritib qo'yilgan)
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "7985206085").split(",")]

# Sizning botingiz manzili (Reklama uchun)
BOT_URL = "https://t.me/kino_livebot"

# Vercel KV (Redis) sozlamalari
KV_URL = os.environ.get("KV_REST_API_URL")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN")

async def kv_get(key):
    """Asinxron KV ma'lumot olish"""
    if not KV_URL or not KV_TOKEN:
        return None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{KV_URL}/get/{key}",
                headers={"Authorization": f"Bearer {KV_TOKEN}"},
                timeout=10.0
            )
            if response.status_code == 200:
                res_json = response.json()
                val = res_json.get("result")
                return json.loads(val) if val else None
    except Exception as e:
        logger.error(f"KV Get xato: {e}")
    return None

async def kv_set(key, value):
    """Asinxron KV ma'lumot saqlash"""
    if not KV_URL or not KV_TOKEN:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{KV_URL}/set/{key}",
                headers={"Authorization": f"Bearer {KV_TOKEN}"},
                content=json.dumps(value, ensure_ascii=False).encode('utf-8'),
                timeout=10.0
            )
            logger.info(f"KV Set muvaffaqiyatli: {key}")
    except Exception as e:
        logger.error(f"KV Set xato: {e}")

# ============= DOIMIY HOLATLAR (VERCEL UCHUN) =============
async def get_state(user_id):
    """Vercel KV dan foydalanuvchi holatini qaytaradi"""
    state_data = await kv_get(f"state_{user_id}")
    if state_data:
        return state_data.get("state"), state_data.get("data", {})
    return None, {}

async def set_state(user_id, state, data=None):
    """Foydalanuvchi holatini Vercel KV ga saqlaydi"""
    await kv_set(f"state_{user_id}", {"state": state, "data": data or {}})

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= MA'LUMOTLARNI YUKLASH =============
async def load_data():
    # 1. Avval Vercel KV dan tekshiramiz
    cached_data = await kv_get("kino_bot_data")
    if cached_data:
        logger.info("Ma'lumotlar Vercel KV dan yuklandi.")
        return cached_data

    # 2. Agar KV bo'lmasa lokal fayldan tekshiramiz
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    
    return {
        "kinolar": {},
        "guruhlar": [],
        "foydalanuvchilar": {},
        "statistika": {"jami_qidiruvlar": 0, "jami_foydalanuvchilar": 0},
        "majburiy_kanallar": []
    }

async def save_data(data):
    # 1. Lokal faylga saqlash
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Faylga saqlashda xato: {e}")

    # 2. Vercel KV ga saqlash
    await kv_set("kino_bot_data", data)

data = {
    "kinolar": {},
    "guruhlar": [],
    "foydalanuvchilar": {},
    "statistika": {"jami_qidiruvlar": 0, "jami_foydalanuvchilar": 0},
    "majburiy_kanallar": []
}

async def post_init(application: Application) -> None:
    """Bot yangilanganida adminlarni ogohlantiradi va ma'lumotlarni yuklaydi"""
    global _notified_admin, data
    
    # Ma'lumotlarni yuklaymiz
    loaded = await load_data()
    if loaded:
        data.update(loaded)
        data.setdefault("majburiy_kanallar", [])
        logger.info("Ma'lumotlar global o'zgaruvchiga yuklandi.")

    if not _notified_admin:
        _notified_admin = True
        for admin_id in ADMIN_IDS:
            try:
                asyncio.create_task(application.bot.send_message(
                    chat_id=int(admin_id),
                    text="✅ **Loyiha muvaffaqiyatli yangilandi!**\n\nBarcha yangi funksiyalar (Asinxron KV, Regex, Sharing) faol. 🚀",
                    parse_mode="Markdown"
                ))
            except: pass

# ============= YORDAMCHI FUNKSIYALAR =============
async def register_user(user_id, username, first_name):
    user_id_str = str(user_id)
    if user_id_str not in data["foydalanuvchilar"]:
        data["foydalanuvchilar"][user_id_str] = {
            "username": username,
            "first_name": first_name,
            "qidiruvlar": 0,
            "qoshilgan_vaqt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        await save_data(data)

# ============= OBUNA TEKSHIRISH =============
async def check_subscription(user_id, bot):
    if not data.get("majburiy_kanallar"):
        return True
    
    for kanal in data["majburiy_kanallar"]:
        try:
            chat_id = kanal.get("chat_id")
            if chat_id:
                # String sifatida bo'sh joylarni tozalash
                chat_id = str(chat_id).strip()
                member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                if member.status in ['left', 'kicked']:
                    return False
        except Exception as e:
            logger.error(f"Kanalni tekshirish xatosi ({chat_id}): {e}")
            return "error"
    return True

def get_subscription_keyboard():
    keyboard = []
    kanallar = data.get("majburiy_kanallar", [])
    for kanal in kanallar:
        url = kanal.get('url', BOT_URL)
        name = kanal.get('name', "📢 Obuna bo'lish")
        keyboard.append([InlineKeyboardButton(f"{name}", url=url)])
    keyboard.append([InlineKeyboardButton("✅ Obunani tasdiqlash", callback_data="check_sub")])
    return InlineKeyboardMarkup(keyboard)

# ============= ADMIN PANEL UI =============
def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Kino Qo'shish", callback_data="admin_add")],
        [InlineKeyboardButton("🗑 Kino O'chirish", callback_data="admin_del")],
        [InlineKeyboardButton("🔗 Majburiy kanal qo'shish", callback_data="admin_add_ch")],
        [InlineKeyboardButton("🗑 Majburiy kanal o'chirish", callback_data="admin_del_ch")],
        [InlineKeyboardButton("📢 Reklama Tarqatish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Tahlil Qilish", callback_data="admin_stats")]
    ])

# ============= GURUHLARNI KUZATISH (AVTOMATIK) =============
async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Bu funksiya bot biror guruhga yoki kanalga qo'shilganda yoki admin qilinganda
    avtomatik ishga tushadi va chat_id ni o'ziga saqlab oladi.
    """
    result = update.my_chat_member
    if not result:
        return
        
    chat_id = str(result.chat.id)
    chat_type = result.chat.type
    new_status = result.new_chat_member.status
    
    # Faqat guruhlar yoki kanallarni (shaxsiy PMdan tashqari) e'tiborga olamiz
    if chat_type in ['group', 'supergroup', 'channel']:
        # Kirdi yoki admin bo'ldi
        if new_status in [ChatMember.ADMINISTRATOR, ChatMember.MEMBER]:
            if chat_id not in data["guruhlar"]:
                data["guruhlar"].append(chat_id)
                save_data(data)
                logger.info(f"Bot yangi chat/kanalga qo'shildi: {chat_id}")
                
        # Chiqib ketdi yoki tepildi
        elif new_status in [ChatMember.LEFT, ChatMember.KICKED, ChatMember.RESTRICTED]:
            if chat_id in data["guruhlar"]:
                data["guruhlar"].remove(chat_id)
                save_data(data)
                logger.info(f"Bot chat/kanaldan o'chirildi: {chat_id}")

# ============= START VA KOD BILAN KIRISH =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await register_user(user.id, user.username, user.first_name)
    
    # Gruppalarda start komandasiga bot javob bermasligi yaxshiroq
    if update.effective_chat.type != 'private':
        return
        
    kutilayotgan_kod = None
    if context.args and len(context.args) > 0:
        kutilayotgan_kod = context.args[0].strip()
        
    # Majburiy obunani tekshirish eng birinchi qilinadi
    if data["majburiy_kanallar"]:
        is_subscribed = await check_subscription(user.id, context.bot)
        if not is_subscribed:
            if kutilayotgan_kod:
                context.user_data["start_kod"] = kutilayotgan_kod
                
            await update.message.reply_text(
                f"Assalomu alaykum **{user.first_name}**!\n\n⚠️ Botdan foydalanish uchun quyidagi raqamli homiylarimizga obuna bo'lishingiz shart:",
                reply_markup=get_subscription_keyboard(),
                parse_mode="Markdown"
            )
            return

    # Agar obunasi bor bo'lsa va kod kiritilgan bo'lsa darhol kinoni beramiz
    if kutilayotgan_kod:
        await process_movie_request(update, context, kutilayotgan_kod)
        return
    
    # Oddiy start / Admin panelga kirish
    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            f"👑 **Admin Panelga Xush Kelibsiz!**\n\nQuyidagi tugmalardan birini tanlab maqsadga ko'ching:",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
    else:
        welcome_text = (
            f"🎬 **Assalomu alaykum {user.first_name}!**\n\n"
            f"Botga xush kelibsiz! Kino kodini shu yerga yuborsangiz bot sizga filmni darrov tashlab beradi.\n\n"
            f"💻 *Turg'unboyev Biloldin* tomonidan maxsus yaratildi!"
        )
        await update.message.reply_text(welcome_text, parse_mode="Markdown")

# ============= XABARLARNI QABUL QILISH (ADMIN VA USER) =============
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        return
        
    user = update.effective_user
    text = update.message.text.strip()
    
    state, state_data = get_state(user.id)
    
    # ------------------ ADMIN HOLATI (STATE) ------------------
    if user.id in ADMIN_IDS:
        if state == "WAIT_DEL_CODE":
            kod = text
            if str(kod) in data["kinolar"]:
                del data["kinolar"][str(kod)]
                save_data(data)
                await update.message.reply_text(f"🗑 `{kod}` kodli kino o'chirib tashlandi!", parse_mode="Markdown", reply_markup=get_admin_keyboard())
            else:
                await update.message.reply_text(f"❌ `{kod}` degan kod topilmadi!", parse_mode="Markdown", reply_markup=get_admin_keyboard())
            set_state(user.id, None)
            return

        elif state == "WAIT_CH_ID":
            set_state(user.id, "WAIT_CH_URL", {"temp_ch_id": text})
            await update.message.reply_text("2️⃣ Endi bu kanal uchun Invite Link (Ssilka) yuboring:\n(Masalan: `https://t.me/kino_uz`):")
            return
            
        elif state == "WAIT_CH_URL":
            state_data["temp_ch_url"] = text
            set_state(user.id, "WAIT_CH_NAME", state_data)
            await update.message.reply_text("3️⃣ Foydalanuvchilar obuna bo'lish tugmasida nima deb yozilib tursin?\n(Masalan: `📢 Bosh Kanalimiz`):")
            return
            
        elif state == "WAIT_CH_NAME":
            ch_name = text
            yangi_kanal = {
                "chat_id": state_data.get("temp_ch_id"),
                "url": state_data.get("temp_ch_url"),
                "name": ch_name
            }
            data["majburiy_kanallar"].append(yangi_kanal)
            save_data(data)
            
            await update.message.reply_text(f"✅ Majburiy kanal saqlandi!\n\nNomi: {ch_name}\nSsilka: {yangi_kanal['url']}\nID: {yangi_kanal['chat_id']}", reply_markup=get_admin_keyboard())
            set_state(user.id, None)
            return

        elif state == "WAIT_AD_TEXT":
            ad_text = update.message.text_html
            set_state(user.id, None)
            
            await update.message.reply_text("🚀 Reklama tarqatish boshlandi (Guruhlar va Foydalanuvchilar)...")
            
            success_gr = 0
            success_usr = 0
            failed_gr = 0
            
            failed_ids = []
            for c_id in data.get("guruhlar", []):
                try:
                    await context.bot.send_message(chat_id=int(c_id), text=ad_text, parse_mode="HTML")
                    success_gr += 1
                    await asyncio.sleep(0.3)
                except:
                    failed_gr += 1
                    failed_ids.append(c_id)
            
            if failed_ids:
                for f_id in failed_ids:
                    if f_id in data["guruhlar"]: data["guruhlar"].remove(f_id)
                save_data(data)

            for u_id in data.get("foydalanuvchilar", {}):
                try:
                    await context.bot.send_message(chat_id=int(u_id), text=ad_text, parse_mode="HTML")
                    success_usr += 1
                    await asyncio.sleep(0.05)
                except:
                    pass
                
            await update.message.reply_text(
                f"📢 **Reklama yakunlandi:**\n\n"
                f"👥 Foydalanuvchilar: {success_usr} ta\n"
                f"🏢 Guruhlar: {success_gr} ta\n"
                f"❌ Yopilgan guruhlar: {failed_gr} ta",
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            return

    # ------------------ ODDY FOYDALANUVCHI (KINO QIDIRISH) ------------------
    if data.get("majburiy_kanallar"):
        is_subscribed = await check_subscription(user.id, context.bot)
        if not is_subscribed:
            set_state(user.id, "WAIT_SUB_FOR_MOVIE", {"start_kod": text})
            await update.message.reply_text(
                "⚠️ **Kino izlashdan oldin homiy kanallarimizga a'zo bo'ling!**\n\n"
                "Obuna bo'lgandan so'ng *Tasdiqlash* tugmasini bosishingiz bilanoq so'ragan kinongizni beraman:",
                reply_markup=get_subscription_keyboard(),
                parse_mode="Markdown"
            )
            return

    await process_movie_request(update, context, text)

# ============= KINO QIDIRISH (CORE LOGIC) =============
async def process_movie_request(update: Update, context: ContextTypes.DEFAULT_TYPE, search_term: str):
    user_id = update.effective_user.id
    
    kod = search_term.strip()
    if str(kod) not in data["kinolar"]:
        matches = []
        for k, info in data["kinolar"].items():
            desc = info.get("desc", "").lower()
            if kod.lower() in desc:
                matches.append((k, info.get("desc", "🎬 Kino")))
        
        if not matches:
            await context.bot.send_message(chat_id=user_id, text=f"❌ Kechirasiz!\n`{kod}` bo'yicha hech qanday kino topilmadi.")
            return
            
        if len(matches) > 1:
            result_text = "🔎 **Mana nimalarni topdim:**\n\n"
            for m_kod, m_name in matches[:10]:
                result_text += f"🔹 `{m_kod}` - {m_name}\n"
            result_text += "\nKino kodini yozib yuboring."
            await context.bot.send_message(chat_id=user_id, text=result_text, parse_mode="Markdown")
            return
        else:
            kod = matches[0][0]

    movie_info = data["kinolar"][str(kod)]
    
    msg_id = movie_info["msg_id"] if isinstance(movie_info, dict) else movie_info
    chat_id = movie_info["chat_id"] if isinstance(movie_info, dict) else update.effective_chat.id
    kino_desc = movie_info.get("desc", "🎬 Kino") if isinstance(movie_info, dict) else "🎬 Kino"
    
    status_msg = await context.bot.send_message(chat_id=user_id, text="🔍 Kino qidirilyapti, kutib turing...")
    
    caption_text = (
        f"🎬 **Kino nomi:** {kino_desc}\n"
        f"🔑 **Kino kodi:** `{kod}`\n\n"
        f"👇 Kino botimiz orqali hoziroq ko'ring:\n"
        f"👉 {BOT_URL}"
    )
    
    # Share link yaratish
    share_url = f"https://t.me/share/url?url={BOT_URL}?start={kod}&text=🎬 {kino_desc} - ushbu kinoni bot orqali ko'ring!"
    share_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️ Do'stlarga ulashish", url=share_url)]
    ])
    
    try:
        await context.bot.copy_message(
            chat_id=user_id, 
            from_chat_id=chat_id, 
            message_id=msg_id, 
            caption=caption_text, 
            parse_mode="Markdown",
            protect_content=True, # Forward qilishni taqiqlash
            reply_markup=share_kb
        )
        await status_msg.delete()
        
        data["statistika"]["jami_qidiruvlar"] += 1
        str_uid = str(user_id)
        if str_uid in data["foydalanuvchilar"]:
            data["foydalanuvchilar"][str_uid]["qidiruvlar"] += 1
        save_data(data)
    except Exception as e:
        logger.error(f"Copy message xildi: {e}")
        await status_msg.edit_text(f"❌ Xatolik!\nKino baza kanalidan (Yopiq kanaldan) butunlay o'chirib yuborilgan ko'rinadi.")

# ============= ADMIN QISMI (VIDEO QABUL QILISH) =============
async def handle_admin_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    # Vercel-da xotira o'chib ketishi sababli, bizga bitta xabarda 
    # (Video + Caption) ma'lumotlar kelishi eng ishonchli yo'l.
    
    caption = update.message.caption
    if not caption:
        # Agar caption bo'lmasa, uni WAIT_MOVIE holati deb hisoblaymiz va yo'riqnoma beramiz
        await update.message.reply_text(
            "⚠️ **Kino qo'shish tartibi o'zgardi (Vercel uchun):**\n\n"
            "Kinoni yuborishda uning **izoh (podpis)** qismiga quyidagi formatda yozing:\n"
            "`Kod / Kino nomi va izohi` \n\n"
            "**Masalan:** \n"
            "`123 / O'rgimchak odam (Sarguzasht)`",
            parse_mode="Markdown"
        )
        return

    try:
        import re
        # Captiondan kodi va izohni ajratib olamiz
        # Avval "ID: 123" yoki "Kod: 123" ko'rinishidagi raqamni qidiramiz
        match = re.search(r'(?:ID|Kod|Kodi|id|kod|kodi)[:\s]+([\w\d]+)', caption)
        
        if match:
            kod = match.group(1).strip()
            desc = caption.replace(match.group(0), "").strip() # Kod qismini olib tashlaymiz
        else:
            # Agar maxsus ID topilmasa, birinchi so'zni kod deb olamiz
            parts = caption.split(None, 1)
            kod = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else "🎬 Ajoyib kino"
        
        status_msg = await update.message.reply_text(f"📥 Kino `{kod}` kodi bilan saqlanyapti...")
        
        # Kinoni storage kanalga nusxalaymiz
        if STORAGE_CHANNEL_ID:
            sent_msg = await context.bot.copy_message(
                chat_id=STORAGE_CHANNEL_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            chat_id_to_store = STORAGE_CHANNEL_ID
            msg_id_to_store = sent_msg.message_id
        else:
            chat_id_to_store = update.message.chat_id
            msg_id_to_store = update.message.message_id
            
        # Bazaga saqlaymiz
        data["kinolar"][str(kod)] = {
            "msg_id": msg_id_to_store,
            "chat_id": chat_id_to_store,
            "desc": desc
        }
        save_data(data)
        
        # Backup JSON to telegram (Removed as KV handles it)
        
        await status_msg.delete()
        await update.message.reply_text(
            f"✅ **Kino muvaffaqiyatli saqlandi!**\n\n"
            f"🔑 Kodi: `{kod}`\n"
            f"📝 Izoh: {desc}",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard()
        )
        
        # Reklama tarqatish
        await broadcast_movie(context, kod)
        
    except Exception as e:
        logger.error(f"Kino saqlashda xato: {e}")
        await update.message.reply_text(f"❌ Xatolik yuz berdi: {e}")

# ============= CALLBACK TUGMALARI =============
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data_cb = query.data
    
    # Oddiy foydalanuvchi obunasini tekshirish tugmasi
    if data_cb == "check_sub":
        is_subscribed = await check_subscription(user_id, context.bot)
        if is_subscribed == "error":
            await query.message.reply_text("❌ XATOLIK! Bot kanalga admin qilinmagan.", parse_mode="Markdown")
        elif is_subscribed:
            # KV dan saqlangan kodni olamiz
            u_state, u_data = get_state(user_id)
            kod_kutilayotgan = u_data.get("start_kod")
            if kod_kutilayotgan:
                await query.message.delete()
                await process_movie_request(update, context, kod_kutilayotgan)
                set_state(user_id, None)
            else:
                user = update.effective_user
                await query.edit_message_text(f"🎬 **Assalomu alaykum {user.first_name}!**\n\n✅ Tasdiqlandi. Kino kodini yozing:", parse_mode="Markdown")
        else:
            await query.message.reply_text("❌ Siz hali obuna bo'lmadingiz!")
            
    # Admin Panel Tugmalari
    elif data_cb.startswith("admin_"):
        if user_id not in ADMIN_IDS:
            return
            
        if data_cb == "admin_add":
            # Kino qo'shish endi bir bosqichli (Handle Media orqali)
            await query.message.reply_text("🎥 Videoni uning ostida ID raqami bilan yuboring.\nMasalan: `284 Anaconda` (podpisiga yozing)")
        
        elif data_cb == "admin_del":
            set_state(user_id, "WAIT_DEL_CODE")
            await query.message.reply_text("🗑 O'chirmoqchi bo'lgan kino kodini yozing:")
            
        elif data_cb == "admin_add_ch":
            set_state(user_id, "WAIT_CH_ID")
            await query.message.reply_text("1️⃣ Kanal ID yoki @username kiriting:")
            
        elif data_cb == "admin_del_ch":
            kanallar = data["majburiy_kanallar"]
            if not kanallar:
                await query.message.reply_text("Kanal yo'q.", reply_markup=get_admin_keyboard())
                return
            keys = [[InlineKeyboardButton(f"❌ {ch['name']}", callback_data=f"del_ch_{idx}")] for idx, ch in enumerate(kanallar)]
            keys.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])
            await query.message.reply_text("O'chirish uchun tanlang:", reply_markup=InlineKeyboardMarkup(keys))
        
        elif data_cb == "admin_broadcast":
            set_state(user_id, "WAIT_AD_TEXT")
            await query.message.reply_text("📝 Reklama matnini yuboring (HTML mumkin):")
            
        elif data_cb == "admin_stats":
            jami_foydalanuvchilar = len(data.get("foydalanuvchilar", {}))
            jami_guruhlar = len(data.get("guruhlar", []))
            jami_kinolar = len(data.get("kinolar", {}))
            jami_qidiruvlar = data.get("statistika", {}).get("jami_qidiruvlar", 0)
            
            matn = (
                f"📊 **Statistika**\n\n"
                f"👤 Foydalanuvchilar: {jami_foydalanuvchilar} ta\n"
                f"🏢 Guruhlar: {jami_guruhlar} ta\n"
                f"🎬 Kinolar: {jami_kinolar} ta\n"
                f"🔍 Qidiruvlar: {jami_qidiruvlar} marta\n"
            )
            await query.message.reply_text(matn, parse_mode="Markdown", reply_markup=get_admin_keyboard())

        elif data_cb == "admin_back":
            set_state(user_id, None)
            await query.message.edit_text("👑 Admin Panel", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

    # Kanal olib tashlash tugmasini bosganda
    elif data_cb.startswith("del_ch_"):
        if user_id not in ADMIN_IDS:
            return
        idx = int(data_cb.split("_")[2])
        if 0 <= idx < len(data["majburiy_kanallar"]):
            olingan = data["majburiy_kanallar"].pop(idx)
            save_data(data)
            await query.message.edit_text(f"✅ **{olingan['name']}** obunalar safidan o'chirildi!", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

# ============= GURUHLARGA REKLAMA TARQATISH =============
async def broadcast_movie(context: ContextTypes.DEFAULT_TYPE, kod: str):
    movie_info = data["kinolar"].get(str(kod), {})
    kino_desc = movie_info.get("desc", "Yangi kino kiritildi!") if isinstance(movie_info, dict) else "Yangi kino kiritildi!"
    
    message_text = (
        f"🎬 **YANGI KINO YUKLANDI!**\n\n"
        f"📝 {kino_desc}\n\n"
        f"📌 **Kino kodi:** `{kod}`\n\n"
        f"👇 Kino botimiz orqali hoziroq ko'ring:\n"
        f"👉 {BOT_URL}?start={kod}"
    )
    
    # data["guruhlar"] va foydalanuvchilarga yuboramiz
    message_text += f"\n\n👥 {len(data.get('foydalanuvchilar', {}))} ta foydalanuvchiga yuborilyapti..."
    
    # Guruhlarga
    for chat_id in data.get("guruhlar", []):
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=message_text, parse_mode="Markdown")
            await asyncio.sleep(0.3)
        except:
            pass
            
    # Foydalanuvchilarga
    for user_id in data.get("foydalanuvchilar", {}):
        try:
            await context.bot.send_message(chat_id=int(user_id), text=message_text, parse_mode="Markdown")
            await asyncio.sleep(0.05)
        except:
            pass

# ============= APPLICATION INITIALIZATION =============
def build_application():
    app = Application.builder().token(TOKEN).build()
    
    app.post_init = post_init
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # User va Admin text xabarlarini qayta ishlash
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # Admin videolarni jo'natishini kutish
    app.add_handler(MessageHandler((filters.VIDEO | filters.Document.ALL | filters.PHOTO), handle_admin_media))
    
    # Bot guruhlarga kiritilishini quloqqa olish
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    
    return app

# ============= MAIN (FOR LOCAL POLLING) =============
async def main():
    app = build_application()
    
    logger.info("Bot ishlashni boshladi (Polling)...")
    logger.info(f"Admin: {ADMIN_IDS}")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Infinite loop
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
