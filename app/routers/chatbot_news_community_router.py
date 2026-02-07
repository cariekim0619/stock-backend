# app/routers/chatbot_news_community_router.py

from fastapi import APIRouter
from app.services.chatbot_community.chatbot_news_community import ChatbotNewsCommunity

router = APIRouter(prefix="/chatbot", tags=["chatbot"])

chatbot = ChatbotNewsCommunity()

@router.post("/community")
def get_community(symbol: str, company_name: str, user_name: str = "사용자"):
    # 1단계: 커뮤니티 요약 → 카카오 포맷으로 반환
    summary = chatbot.get_community_summary(symbol=symbol, company_name=company_name)
    return chatbot.format_community_for_kakao(summary, user_name=user_name)

@router.post("/news")
def get_news(symbol: str, company_name: str):
    # 2단계: 뉴스 요약 → 카카오 포맷으로 반환
    summary = chatbot.get_news_summary(symbol=symbol, company_name=company_name)
    return chatbot.format_news_for_kakao(summary)
