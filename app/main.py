# app/main.py

from dotenv import load_dotenv  # .env 로딩용
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import report_router
from app.routers import chatbot_news_community_router  # ✅ 기존 Chatbot_05
from app.routers import glossary_router  # ✅ Chatbot_03 주식 용어 사전 라우터 추가

# ✅ 앱 시작 시 .env를 한 번만 로드
load_dotenv()

app = FastAPI()

# NOTE:
# allow_credentials=True 와 allow_origins=["*"] 조합은 브라우저 CORS에서 문제가 될 수 있음.
# 지금은 기존 설정 유지하되 운영에서는 origins를 구체 도메인으로 제한하는 것이 더 안전함.
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ 기존 라우터
app.include_router(report_router.router)

# ✅ Chatbot_05 라우터
app.include_router(chatbot_news_community_router.router)

# ✅ Chatbot_03 라우터 추가
app.include_router(glossary_router.router)

@app.get("/")
def health_check():
    return {"status": "ok", "message": "backend is running"}