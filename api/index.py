import os
import json
import asyncio
from fastapi import FastAPI, Request
from telegram import Update
from kino_bot import build_application

app = FastAPI()
ptb_app = build_application()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Kino Bot is live on Vercel!"}

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        
        async with ptb_app:
            await ptb_app.process_update(update)
            
        return {"status": "ok"}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/setup")
async def setup_webhook(request: Request):
    """Webhookni avtomatik sozlaydi"""
    # Vercel URL ni aniqlash
    host = request.headers.get("host")
    if not host:
        return {"error": "Host not found"}
    
    url = f"https://{host}/webhook"
    
    async with ptb_app:
        success = await ptb_app.bot.set_webhook(url=url)
        
    if success:
        return {"status": "success", "url": url, "message": "Webhook muvaffaqiyatli o'rnatildi!"}
    else:
        return {"status": "failed", "message": "Webhookni o'rnatib bo'lmadi."}
