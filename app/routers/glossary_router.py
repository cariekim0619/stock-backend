from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

# 서비스 import
from app.services.chatbot_glossary.chatbot_glossary import ChatbotGlossary

router = APIRouter(prefix="/chatbot/glossary", tags=["Chatbot Glossary"])

# 앱 시작 시 1번만 생성해서 재사용
chatbot = ChatbotGlossary()


# 요청 바디 모델
class GlossaryRequest(BaseModel):
    action: str                      # entry | category | search
    user_input: Optional[str] = None
    category: Optional[str] = None
    segment: Optional[str] = "risk-neutral"
    profile: Optional[Dict[str, Any]] = None


@router.post("")
def handle_glossary(req: GlossaryRequest):
    """
    주식 용어 사전 메인 엔드포인트

    action 종류
    - entry    : 기능 진입
    - category : 카테고리 선택
    - search   : 용어 검색
    """
    try:
        if req.action == "entry":
            return chatbot.format_entry_for_kakao()

        elif req.action == "category":
            if not req.category:
                raise HTTPException(status_code=400, detail="category가 필요합니다.")
            return chatbot.format_category_for_kakao(req.category)

        elif req.action == "search":
            if not req.user_input:
                raise HTTPException(status_code=400, detail="user_input이 필요합니다.")

            result = chatbot.search_and_explain(req.user_input)

            if result["status"] == "found":
                return chatbot.format_explanation_for_kakao(result)
            elif result["status"] == "multiple":
                return chatbot.format_disambiguate_for_kakao(result["candidates"])
            else:
                return chatbot.format_not_found_for_kakao()

        else:
            raise HTTPException(status_code=400, detail="지원하지 않는 action 입니다.")

    except Exception as e:
        # 운영 시에는 logging 추가 권장
        raise HTTPException(status_code=500, detail=str(e))