# app/routers/chatbot_news_community_router.py

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

from app.services.chatbot_community.chatbot_news_community import ChatbotNewsCommunity
from app.utils.ticker_normalizer import resolve_symbol_and_name  # ✅ ticker -> (symbol, company_name)
from app.services.segment_personalization import normalize_segment


class BriefingRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    user_name: str = "사용자"
    segment: Optional[str] = "risk-neutral"
    profile: Optional[Dict[str, Any]] = None
    survey_profile: Optional[Dict[str, Any]] = None
    personalization: Optional[Dict[str, Any]] = None




def _safe_error_kakao(message: str = "지금은 요약 생성이 잠시 불안정해요. 잠시 후 다시 시도해 주세요."):
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": message}}],
            "quickReplies": [{"action": "message", "label": "메인으로", "messageText": "메인으로"}],
        },
    }

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
    if not symbol or not symbol.isdigit() or len(symbol) != 6:
        return _no_stock_kakao(req.ticker)

    segment = normalize_segment(req.segment)
    summary = chatbot.get_community_summary(
        symbol=symbol,
        company_name=company_name,
        segment=segment,
        profile=req.profile or req.survey_profile,
    )
    return chatbot.format_community_for_kakao(summary, user_name=req.user_name)

@router.post("/news")
def chatbot_news(req: BriefingRequest):
    resolved = resolve_symbol_and_name(req.ticker)
    if not resolved:
        return _no_stock_kakao(req.ticker)

    symbol, company_name = resolved
    if not symbol or not symbol.isdigit() or len(symbol) != 6:
        return _no_stock_kakao(req.ticker)

    segment = normalize_segment(req.segment)
    summary = chatbot.get_news_summary(
        symbol=symbol,
        company_name=company_name,
        segment=segment,
        profile=req.profile or req.survey_profile,
    )
    return chatbot.format_news_for_kakao(summary)
