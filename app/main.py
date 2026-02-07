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
# allow_credentials=True 와 allow_origins=["*"] 조합은 브라우저 CORS에서 문제가 될 수 있음.
# 지금은 기존 설정 유지하되, 운영에서는 origins를 도메인으로 제한하거나 allow_credentials=False 권장.
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

# ✅ Chatbot_05 라우터 (prefix="/chatbot" 형태로 만들어둔 router를 include)
app.include_router(chatbot_news_community_router.router)

@app.get("/")
def health_check():
    return {"status": "ok", "message": "backend is running"}
