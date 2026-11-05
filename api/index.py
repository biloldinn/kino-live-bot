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


@app.get("/")
@app.head("/")
async def root():
    return JSONResponse({"status": "Bot is running on Vercel", "bot": "@kino_liive_bot"})


@app.post("/")
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        if "update_id" in data:
            update = Update.de_json(data, ptb_app.bot)
            async with ptb_app:
                await ptb_app.process_update(update)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/webhook")
async def webhook_get():
    return JSONResponse({"status": "webhook ready"})
