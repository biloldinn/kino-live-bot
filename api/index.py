import os
import json
import asyncio
from fastapi import FastAPI, Request
from telegram import Update
from kino_bot import build_application

app = FastAPI()

# Botni bitta marta yaratib olamiz
ptb_app = build_application()

@app.get("/")
async def root():
    return {"status": "Bot is running on Vercel"}

@app.post("/webhook")
async def webhook_handler(request: Request):
    """Telegramdan kelayotgan webhooklarni qabul qiladi"""
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        
        # Application context orqali update-ni qayta ishlaymiz
        async with ptb_app:
            await ptb_app.process_update(update)
            
        return {"status": "ok"}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

# Vercel uchun qo'shimcha so'rov: Webhookni sozlash
@app.get("/set_webhook")
async def set_webhook():
    """Botga webhook URL-ni avtomatik kiritish"""
    # Eslatma: Buni manually ham qilish mumkin: 
    # https://api.telegram.org/bot<TOKEN>/setWebhook?url=<URL>/webhook
    return {"message": "Please set webhook manually using the URL provided in instructions."}
