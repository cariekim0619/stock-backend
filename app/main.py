# app/main.py

from dotenv import load_dotenv  # .env 로딩용
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import report_router
from app.routers import chatbot_news_community_router  # ✅ 새로 추가한 라우터

# ✅ 앱 시작 시 .env를 한 번만 로드 (TAVILY_API_KEY, GEMINI_API_KEY 등)
load_dotenv()

app = FastAPI()

# NOTE:
# allow_credentials=True 와 allow_origins=["*"] 조합�