import os
import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update
from kino_bot import build_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
ptb_app = build_application()

async def handle_update(request: Request):
    """Telegramdan kelgan yangilanishni (update) qabul qilib botga uzatadi"""
    try:
        data = await request.json()
        if "update_id" in data:
            update = Update.de_json(data, ptb_app.bot)
            async with ptb_app:
                await ptb_app.process_update(update)
            return JSONResponse({"status": "ok"})
        return JSONResponse({"status": "ignored", "reason": "no update_id"})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/")
@app.head("/")
async def root():
    return JSONResponse({"status": "Bot is running on Vercel", "bot": "@kino_liive_bot"})

@app.post("/")
async def root_post(request: Request):
    return await handle_update(request)

@app.api_route("/{full_path:path}", methods=["GET", "POST", "HEAD"])
async def catch_all(request: Request, full_path: str):
    if request.method == "POST":
        return await handle_update(request)
    return JSONResponse({"status": "Bot is running on Vercel", "bot": "@kino_liive_bot", "path": full_path})
