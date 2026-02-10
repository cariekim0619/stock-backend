# app/routers/chatbot_news_community_router.py

from fastapi import APIRouter, HTTPException
from app.services.chatbot_community.chatbot_news_community import ChatbotNewsCommunity

from pydantic import BaseModel, Field

class BriefingRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    user_name: str = "사용자"

router = APIRouter(
    prefix="/chatbot",
    tags=["Chatbot News / Community"],
)

chatbot = ChatbotNewsCommunity()

@router.post("/community")
def chatbot_community(req: BriefingRequest):
    summary = chatbot.get_community_summary(ticker=req.ticker)
    return chatbot.format_community_for_kakao(summary)

@router.post("/news")
def chatbot_news(req: BriefingRequest):
    summary = chatbot.get_news_summary(ticker=req.ticker)
    return chatbot.format_news_for_kakao(summary)

