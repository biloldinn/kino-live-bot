import os
import asyncio
import logging
from fastapi import FastAPI, Request, Response
from telegram import Update
from kino_bot import build_application

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
ptb_app = build_application()

@app.get("/")
async def root():
    return {"status": "Bot is running on Vercel", "bot": "@kino_liive_bot"}

@app.head("/")
async def root_head():
    return Response(status_code=200)

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        async with ptb_app:
            await ptb_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/setup")
async def setup_webhook(request: Request):
    host = request.headers.get("host", "")
    url = f"https://{host}/webhook"
    try:
        async with ptb_app:
            success = await ptb_app.bot.set_webhook(url=url)
        if success:
            return {"status": "success", "webhook_url": url}
        return {"status": "failed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
