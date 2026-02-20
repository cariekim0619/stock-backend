# app/routers/chatbot_news_community_router.py

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.chatbot_community.chatbot_news_community import ChatbotNewsCommunity
from app.utils.ticker_normalizer import resolve_symbol_and_name  # ✅ ticker -> (symbol, company_name)


class BriefingRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    user_name: str = "사용자"


router = APIRouter(
    prefix="/chatbot",
    tags=["Chatbot News / Community"],
)

chatbot = ChatbotNewsCommunity()


def _no_stock_kakao(ticker: str) -> dict:
    """없는 종목일 때 카카오 스킬 응답"""
    t = (ticker or "").strip()
    return {
        "version": "2.0",
        "template": {
            "outputs": [{
                "simpleText": {
                    "text": f"❌ '{t}'은(는) 없는 종목이에요.\n정확한 종목명 또는 6자리 종목코드를 입력해주세요."
                }
            }]
        }
    }


@router.post("/community")
def chatbot_community(req: BriefingRequest):
    resolved = resolve_symbol_and_name(req.ticker)
    if not resolved:
        return _no_stock_kakao(req.ticker)

    symbol, company_name = resolved

    # ✅ 서비스는 ticker가 아니라 symbol/company_name을 받는다고 가정하고 호출
    summary = chatbot.get_community_summary(
        symbol=symbol,
        company_name=company_name,
        user_name=req.user_name,
    )
    return chatbot.format_community_for_kakao(summary, user_name=req.user_name)


@router.post("/news")
def chatbot_news(req: BriefingRequest):
    resolved = resolve_symbol_and_name(req.ticker)
    if not resolved:
        return _no_stock_kakao(req.ticker)

    symbol, company_name = resolved

    summary = chatbot.get_news_summary(
        symbol=symbol,
        company_name=company_name,
    )
    return chatbot.format_news_for_kakao(summary)