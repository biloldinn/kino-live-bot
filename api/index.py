import os
import asyncio
import logging
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from telegram import Update
from kino_bot import build_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
ptb_app = build_application()


@app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
async def handle_get(request: Request, full_path: str):
    """GET — har qanday yo'l uchun status qaytaradi"""
    return JSONResponse({"status": "Bot is running on Vercel", "bot": "@kino_liive_bot"})


@app.api_route("/{full_path:path}", methods=["POST"])
async def handle_post(request: Request, full_path: str):
    """POST — Telegram webhookini qayta ishlaydi"""
    try:
        data = await request.json()
        
        # Faqat Telegram update bo'lsa process qilish
        if "update_id" in data:
            update = Update.de_json(data, ptb_app.bot)
            async with ptb_app:
                await ptb_app.process_update(update)
            return JSONResponse({"status": "ok"})
        
        return JSONResponse({"status": "ok", "note": "not a telegram update"})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
