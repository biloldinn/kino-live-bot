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

try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None

# ============= SOZLAMALAR =============
# Bot tokeningiz
TOKEN = os.environ.get("BOT_TOKEN", "8679177935:AAHd2tcTrf_P0F7396UJjJXNjVjNkxL6lw0")  

# Admin IDsi (Sizniki kiritib qo'yilgan)
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "7985206085").split(",")]

# Sizning botingiz manzili (Reklama uchun)
BOT_URL = "https://t.me/kino_livebot"

DATA_FILE = "kino_data.json"
MONGO_URI = os.environ.get("MONGO_URI")

# MongoDB sozlamalari
client = MongoClient(MONGO_URI) if (MongoClient and MONGO_URI) else None
db = client["kino_bot_db"] if client else None
collection = db["bot_data"] if db else None

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============= MA'LUMOTLARNI YUKLASH =============
def load_data():
    # 1. MongoDB-dan yuklashga urinish
    if collection:
        try:
            doc = collection.find_one({"_id": "main_data"})
            if doc:
                logger.info("Ma'lumotlar MongoDB-dan muvaffaqiyatli yuklandi.")
                return doc["content"]
        except Exception as e:
            logger.error(f"MongoDB-dan yuklashda xato: {e}")

    # 2. Local JSON-dan yuklashga urinish (Fallback)
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
        "statistika": {
            "jami_qidiruvlar": 0,
            "jami_foydalanuvchilar": 0
        },
        "majburiy_kanallar": []
    }

def save_data(data):
    # 1. MongoDB-ga saqlash (Asosiy)
    if collection:
        try:
            collection.replace_one(
                {"_id": "main_data"},
                {"_id": "main_data", "content": data},
                upsert=True
            )
            return
        except Exception as e:
            logger.error(f"MongoDB-ga saqlashda xato: {e}")

    # 2. Local JSON-ga saqlash (Fallback)
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"JSON faylga saqlashda xato: {e}")

data = load_data()
data.setdefault("majburiy_kanallar", [])
data.setdefault("kinolar", {})
data.setdefault("guruhlar", [])
data.setdefault("foydalanuvchilar", {})
if "statistika" not in data:
    data["statistika"] = {"jami_qidiruvlar": 0, "jami_foydalanuvchilar": 0}

# ============= YORDAMCHI FUNKSIYALAR =============
def register_user(user_id, username, first_name):
    user_id_str = str(user_id)
    if user_id_str not in data["foydalanuvchilar"]:
        data["foydalanuvchilar"][user_id_str] = {
            "username": username,
            "first_name": first_name,
            "qidiruvlar": 0,
            "qoshilgan_vaqt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        data["statistika"]["jami_foydalanuvchilar"] += 1
        save_data(data)

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
    register_user(user.id, user.username, user.first_name)
    
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
    state = context.user_data.get("state")
    
    # ------------------ ADMIN HOLATI (STATE) ------------------
    if user.id in ADMIN_IDS:
        # KINO QUYISH MATNI (REKLAMA) KUTISH
        if state == "WAIT_DESC":
            context.user_data["temp_desc"] = text
            context.user_data["state"] = "WAIT_CODE"
            await update.message.reply_text(
                "✅ Zo'r!\n\n"
                "✏️ **Endi bu kino uchun maxsus kod kiriting?**\n"
                "(Istalgan son yoki xarf yuborishingiz mumkin: masalan: `111` yoki `KUZ`):",
                parse_mode="Markdown"
            )
            return

        # KINO QUYISH KODINI KUTISH
        elif state == "WAIT_CODE":
            kod = text
            
            # datani saqlaymiz:
            data["kinolar"][str(kod)] = {
                "msg_id": context.user_data.get("temp_msg_id"),
                "chat_id": context.user_data.get("temp_chat_id"),
                "desc": context.user_data.get("temp_desc", "🎬 Ajoyib kino")
            }
            save_data(data)
            
            await update.message.reply_text(
                f"✅ Kino bazaga muvaffaqiyatli saqlandi!\n"
                f"🔑 **Kodi:** `{kod}`", 
                parse_mode="Markdown",
                reply_markup=get_admin_keyboard()
            )
            
            # --------- REKLAMA TARQATISH ---------
            await broadcast_movie(context, kod)
            
            # State ni yakunlash
            context.user_data["state"] = None
            context.user_data["temp_msg_id"] = None
            return
            
        # O'CHIRILADIGAN KODNI KUTISH
        elif state == "WAIT_DEL_CODE":
            kod = text
            if str(kod) in data["kinolar"]:
                del data["kinolar"][str(kod)]
                save_data(data)
                await update.message.reply_text(f"🗑 `{kod}` kodli kino o'chirib tashlandi!", parse_mode="Markdown", reply_markup=get_admin_keyboard())
            else:
                await update.message.reply_text(f"❌ `{kod}` degan kod topilmadi!", parse_mode="Markdown", reply_markup=get_admin_keyboard())
            context.user_data["state"] = None
            return

        # MAJBURIY KANAL QO'SHISH BOSQICHLARI
        elif state == "WAIT_CH_ID":
            context.user_data["temp_ch_id"] = text
            context.user_data["state"] = "WAIT_CH_URL"
            await update.message.reply_text("2️⃣ Endi bu kanal uchun Invite Link (Ssilka) yuboring:\n(Masalan: `https://t.me/kino_uz`):")
            return
            
        elif state == "WAIT_CH_URL":
            context.user_data["temp_ch_url"] = text
            context.user_data["state"] = "WAIT_CH_NAME"
            await update.message.reply_text("3️⃣ Foydalanuvchilar obuna bo'lish tugmasida nima deb yozilib tursin?\n(Masalan: `📢 Bosh Kanalimiz`):")
            return
            
        elif state == "WAIT_CH_NAME":
            ch_name = text
            yangi_kanal = {
                "chat_id": context.user_data.get("temp_ch_id"),
                "url": context.user_data.get("temp_ch_url"),
                "name": ch_name
            }
            data["majburiy_kanallar"].append(yangi_kanal)
            save_data(data)
            
            await update.message.reply_text(f"✅ Majburiy kanal saqlandi!\n\nNomi: {ch_name}\nSsilka: {yangi_kanal['url']}\nID: {yangi_kanal['chat_id']}", reply_markup=get_admin_keyboard())
            context.user_data["state"] = None
            return

    # ------------------ ODDY FOYDALANUVCHI (KINO QIDIRISH) ------------------
    if data.get("majburiy_kanallar"):
        is_subscribed = await check_subscription(user.id, context.bot)
        if not is_subscribed:
            context.user_data["start_kod"] = text
            await update.message.reply_text(
                "⚠️ **Kino izlashdan oldin homiy kanallarimizga a'zo bo'ling!**\n\n"
                "Obuna bo'lgandan so'ng *Tasdiqlash* tugmasini bosishingiz bilanoq so'ragan kinongizni beraman:",
                reply_markup=get_subscription_keyboard(),
                parse_mode="Markdown"
            )
            return

    await process_movie_request(update, context, text)

# ============= KINO QIDIRISH (CORE LOGIC) =============
async def process_movie_request(update: Update, context: ContextTypes.DEFAULT_TYPE, kod: str):
    user_id = update.effective_user.id
    
    if str(kod) not in data["kinolar"]:
        await context.bot.send_message(chat_id=user_id, text=f"❌ Kechirasiz!\n`{kod}` raqamli kino xotiradan topilmadi yohud yaqinda o'chirilgan.")
        return
        
    movie_info = data["kinolar"][str(kod)]
    
    msg_id = movie_info["msg_id"] if isinstance(movie_info, dict) else movie_info
    chat_id = movie_info["chat_id"] if isinstance(movie_info, dict) else update.effective_chat.id
    kino_desc = movie_info.get("desc", "🎬 Kino") if isinstance(movie_info, dict) else "🎬 Kino"
    
    status_msg = await context.bot.send_message(chat_id=user_id, text="🔍 Kino yuborilyapti, kutib turing...")
    
    caption_text = f"{kino_desc}\n\n🔑 Kino kodi: `{kod}`\n\n👇 Kino botimiz orqali hoziroq ko'ring:\n👉 {BOT_URL}"
    
    try:
        await context.bot.copy_message(chat_id=user_id, from_chat_id=chat_id, message_id=msg_id, caption=caption_text, parse_mode="Markdown")
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
    if user_id not in ADMIN_IDS or update.effective_chat.type != 'private':
        return
        
    if context.user_data.get("state") == "WAIT_MOVIE":
        try:
            # Videoning qayerdan kelganini eslab qolamiz
            context.user_data["temp_msg_id"] = update.message.message_id
            context.user_data["temp_chat_id"] = update.message.chat_id
            
            context.user_data["state"] = "WAIT_DESC"
            
            await update.message.reply_text(
                "✅ Kino qabul qilindi!\n\n"
                "📝 **Endi ushbu kino uchun qisqa izoh/reklama matnini yuboring:**\n"
                "(Bu matn foydalanuvchilar kinoni qidirganida videoning tagida ko'rinib turadi)",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Xatolik yuz berdi: {e}")
        return

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
            await query.message.reply_text("❌ **UZR XATOLIK!**\n\nBu botga qo'shilgan homiy kanal yoki guruhda botimiz **ADMIN qilib belgilanmagan** yoki Adminlar kanal nomini xato kiritgan!\n\nBot u yerdagi obuna tekshira olmaydi, kanal Adminlari avval xatoni tuzatishi shart!", parse_mode="Markdown")
        elif is_subscribed:
            kod_kutilayotgan = context.user_data.get("start_kod")
            if kod_kutilayotgan:
                await query.message.delete()
                await process_movie_request(update, context, kod_kutilayotgan)
                context.user_data["start_kod"] = None
            else:
                user = update.effective_user
                await query.edit_message_text(
                    f"🎬 **Assalomu alaykum {user.first_name}!**\n\n✅ Obuna tasdiqlandi. Menga to'g'ridan to'g'ri kino kodini kiriting:", 
                    parse_mode="Markdown"
                )
        else:
            await query.message.reply_text("❌ Kechirasiz, siz barcha kanallarga to'liq obuna bo'lmadingiz!")
            
    # Admin Panel Tugmalari
    elif data_cb.startswith("admin_"):
        if user_id not in ADMIN_IDS:
            return
            
        if data_cb == "admin_add":
            context.user_data["state"] = "WAIT_MOVIE"
            await query.message.reply_text("🎥 Menga kinoni jo'nating (Avval video/fayl ni yuboring):")
        
        elif data_cb == "admin_del":
            context.user_data["state"] = "WAIT_DEL_CODE"
            await query.message.reply_text("🗑 O'chirmoqchi bo'lgan kinongizni MAXSUS KODINI xato qilmasdan yozib yuboring:")
            
        elif data_cb == "admin_add_ch":
            context.user_data["state"] = "WAIT_CH_ID"
            await query.message.reply_text("1️⃣ Majburiy kanal/guruhning ko'rinmas ID raqamini yoki @Username ni kiriting:\n(Masalan: `@kino_uz` yoki `-1001234567890`)")
            
        elif data_cb == "admin_del_ch":
            kanallar = data["majburiy_kanallar"]
            if not kanallar:
                await query.message.reply_text("Hozircha hech qanday majburiy kanal yo'q.", reply_markup=get_admin_keyboard())
                return
            keys = []
            for idx, ch in enumerate(kanallar):
                keys.append([InlineKeyboardButton(f"❌ {ch['name']}", callback_data=f"del_ch_{idx}")])
            keys.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])
            await query.message.reply_text("Qaysi kanalni olib tashlashni tanlang:", reply_markup=InlineKeyboardMarkup(keys))
            
        elif data_cb == "admin_stats":
            stats = data["statistika"]
            guruh_soni = len(data["guruhlar"])
            kino_soni = len(data["kinolar"])
            matn = (
                f"📊 **Tahlil (Statistika)**\n\n"
                f"👥 Foydalanuvchilar: {stats['jami_foydalanuvchilar']} ta\n"
                f"🎬 Bazadagi kinolar: {kino_soni} ta kino\n"
                f"🔍 Jami qidiruvlar: {stats['jami_qidiruvlar']} marta qidirilgan\n"
                f"📢 Kuzatuvdagi (Reklama boradigan) tarmoqlar: {guruh_soni} ta guruh/kanal\n"
            )
            await query.message.reply_text(matn, parse_mode="Markdown", reply_markup=get_admin_keyboard())

        elif data_cb == "admin_back":
            await query.message.edit_text("👑 **Admin Panelga Xush Kelibsiz!**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

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
    
    # data["guruhlar"] dagi barcha narsaga yuboramiz
    failed_groups = []
    
    for chat_id in data.get("guruhlar", []):
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=message_text, parse_mode="Markdown")
            await asyncio.sleep(0.5) # Flood wait oldini olish uchun
        except Exception as e:
            logger.error(f"{chat_id} guruhiga xabar yetkazilmay qoldi: {e}")
            failed_groups.append(chat_id)
            
    # Xato bo'lgan/Botni haydab yuborgan guruhlarni ro'yxatdan tozalash
    if failed_groups:
        for f in failed_groups:
            data["guruhlar"].remove(f)
        save_data(data)

# ============= APPLICATION INITIALIZATION =============
def build_application():
    app = Application.builder().token(TOKEN).build()
    
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
