# app/routers/chatbot_news_community_router.py

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

from app.services.chatbot_community.chatbot_news_community import ChatbotNewsCommunity
from app.utils.ticker_normalizer import clean_stock_query_text, resolve_symbol_and_name  # ticker -> (symbol, company_name)
from app.services.segment_personalization import normalize_segment


class BriefingRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    symbol: Optional[str] = None
    company_name: Optional[str] = None
    user_name: str = "사용자"
    segment: Optional[str] = "risk-neutral"
    profile: Optional[Dict[str, Any]] = None
    survey_profile: Optional[Dict[str, Any]] = None
    personalization: Optional[Dict[str, Any]] = None


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


def _resolve_request_stock(req: BriefingRequest) -> Optional[tuple[str, str]]:
    """Lambda가 보낸 symbol/company_name을 우선 사용하고, 없으면 정규화 검색한다."""
    symbol = (req.symbol or "").strip()
    company_name = (req.company_name or "").strip()

    if symbol.isdigit() and len(symbol) == 6:
        resolved = resolve_symbol_and_name(symbol)
        if resolved:
            return resolved
        if company_name:
            # Lambda에서 이미 S3 stock universe로 확인한 경우 EC2 캐시 일시 장애에도 통과시킨다.
            return symbol, company_name

    probes = [req.ticker, company_name, clean_stock_query_text(req.ticker)]
    seen = set()
    for probe in probes:
        q = (probe or "").strip()
        if not q or q in seen:
            continue
        seen.add(q)
        resolved = resolve_symbol_and_name(q)
        if resolved:
            return resolved
    return None


@router.post("/community")
def chatbot_community(req: BriefingRequest):
    resolved = _resolve_request_stock(req)
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
    resolved = _resolve_request_stock(req)
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
