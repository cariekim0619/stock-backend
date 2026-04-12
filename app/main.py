# app/main.py

from dotenv import load_dotenv  # .env 로딩용
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import report_router
from app.routers import chatbot_news_community_router
from app.routers import glossary_router
from app.routers import chatbot_transaction_router
from app.routers import chatbot_favorites_router
from app.utils.ticker_normalizer import warm_stock_universe_cache

# ✅ 앱 시작 시 .env를 한 번만 로드
load_dotenv()

app = FastAPI()

# NOTE:
# allow_credentials=True 와 allow_origins=["*"] 조합은
# 브라우저 CORS 환경에서 문제가 될 수 있음. 조심할 것.
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Chatbot_02 라우터
app.include_router(report_router.router)

# ✅ Chatbot_05 라우터
app.include_router(chatbot_news_community_router.router)

# ✅ Chatbot_03 라우터
app.include_router(glossary_router.router)

# ✅ Chatbot_04 라우터
app.include_router(chatbot_transaction_router.router)

# ✅ Chatbot_06 라우터
app.include_router(chatbot_favorites_router.router)


@app.get("/")
def health_check():
    return {"status": "ok", "message": "backend is running"}


@app.on_event("startup")
def preload_stock_universe_cache():
    try:
        status = warm_stock_universe_cache()
        print(
            f"[startup] stock universe ready: "
            f"{status.get('item_count') or len(status.get('items') or [])} items"
        )
    except Exception as e:
        print(f"[startup] stock universe warmup failed: {e}")