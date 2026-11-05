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


@app.api_route("/{full_path:path}", methods=["GET", "HEAD", "POST"])
async def catch_all(request: Request, full_path: str):
    path = request.url.path.rstrip("/")

    # ROOT
    if path == "" or path == "/":
        return JSONResponse({"status": "Bot is running on Vercel", "bot": "@kino_liive_bot"})

    # WEBHOOK
    if path == "/webhook":
        if request.method == "POST":
            try:
                data = await request.json()
                update = Update.de_json(data, ptb_app.bot)
                async with ptb_app:
                    await ptb_app.process_update(update)
                return JSONResponse({"status": "ok"})
            except Exception as e:
                logger.error(f"Webhook error: {e}")
                return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
        return JSONResponse({"status": "webhook endpoint ready"})

    # SETUP
    if path == "/setup":
        host = request.headers.get("host", "")
        url = f"https://{host}/webhook"
        try:
            async with ptb_app:
                success = await ptb_app.bot.set_webhook(url=url)
            return JSONResponse({"status": "success" if success else "failed", "webhook_url": url})
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

    return JSONResponse({"status": "ok", "path": path})
