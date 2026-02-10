# app/routers/chatbot_news_community_router.py

from fastapi import APIRouter, HTTPException

from app.services.chatbot_community.chatbot_news_community import ChatbotNewsCommunity

router = APIRouter(
    prefix="/chatbot",
    tags=["Chatbot News / Community"],
)

chatbot = ChatbotNewsCommunity()


@router.post("/community")
def chatbot_community(
    ticker: str,
    user_name: str = "사용자",
):
    """
    커뮤니티 요약
    ✅ ticker 하나만 받는다 (종목명/코드 모두 가능)
    """
    ticker = (ticker or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    summary = chatbot.get_community_summary(ticker=ticker)

    return chatbot.format_community_for_kakao(
        summary=summary,
        user_name=user_name,
    )


@router.post("/news")
def chatbot_news(
    ticker: str,
):
    """
    뉴스 요약
    ✅ ticker 하나만 받는다 (종목명/코드 모두 가능)
    """
    ticker = (ticker or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    summary = chatbot.get_news_summary(ticker=ticker)

    return chatbot.format_news_for_kakao(summary)
