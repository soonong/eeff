from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import router

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="AI 입찰 공고 분석 시스템", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)
