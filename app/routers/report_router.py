from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app.services.report_service import generate_report
from app.services.chatbot_report.chatbot_stock_report import ChatbotStockReport

router = APIRouter(
    prefix="/api/stocks",
    tags=["Stock Reports"],
)


# ---------------------------
# 일반 리포트 (웹용)
# ---------------------------

class ReportRequest(BaseModel):
    ticker: str


@router.post("/report")
def get_report(request: ReportRequest):
    ticker = (request.ticker or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    result = generate_report(ticker)

    if not isinstance(result, dict) or "version" not in result or "template" not in result:
        raise HTTPException(status_code=500, detail="invalid kakao skill format")

    return result


# ---------------------------
# 챗봇 전용 요청 모델
# ---------------------------

class ChatbotReportRequest(BaseModel):
    mode: str
    ticker: Optional[str] = ""
    uuid: Optional[str] = ""
    section: Optional[str] = ""
    user_name: Optional[str] = "사용자"
    list_type: Optional[str] = ""
    stocks: Optional[List[str]] = None


# ---------------------------
# 공통 Kakao 응답 생성기
# ---------------------------

def kakao_simple(text: str, quicks: Optional[List[dict]] = None) -> dict:
    payload = {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}]
        }
    }
    if quicks:
        payload["template"]["quickReplies"] = quicks
    return payload


# ---------------------------
# 고정 화면들 (기획안 반영)
# ---------------------------

def entry_screen():
    text = (
        "⬛️ 종목 리포트 기능에 대해 알려드릴게요\n\n"
        "➊ 관심 있는 종목의 투자 리포트를 확인할 수 있어요\n"
        "➋ 종목명을 직접 입력해서 바로 리포트를 볼 수도 있어요\n"
        "➌ 보유 종목과 관심 종목은 계좌가 연동되어 있을 때 확인할 수 있어요\n\n"
        "아래 버튼을 눌러 원하는 방법을 선택해 주세요!"
    )
    quicks = [
        {"action": "message", "label": "관심 종목 확인하기", "messageText": "관심 종목 확인하기"},
        {"action": "message", "label": "보유 종목 확인하기", "messageText": "보유 종목 확인하기"},
        {"action": "message", "label": "종목 직접 입력", "messageText": "종목 직접 입력"},
        {"action": "message", "label": "종목 리포트 종료", "messageText": "종목 리포트 종료"},
    ]
    return kakao_simple(text, quicks)


def account_required_screen():
    text = (
        "해당 기능은 계좌 연결 후 이용할 수 있습니다 :)\n\n"
        "계좌를 연결하시면,\n"
        "보유 종목과 관심 종목의 리포트를 바로 확인할 수 있어요!\n\n"
        "💡 계좌를 연결하지 않아도\n"
        "종목명을 직접 입력하면 리포트를 확인할 수 있어요 :D"
    )
    quicks = [
        {"action": "message", "label": "계좌 연결하기", "messageText": "주식계좌연결"},
        {"action": "message", "label": "종목 직접 입력", "messageText": "종목 직접 입력"},
        {"action": "message", "label": "이전으로", "messageText": "이전으로"},
    ]
    return kakao_simple(text, quicks)


def stock_input_prompt_screen():
    text = "종목명 또는 종목 코드를 입력해 주세요!"
    quicks = [{"action": "message", "label": "이전으로", "messageText": "이전으로"}]
    return kakao_simple(text, quicks)


def stock_not_found_screen():
    text = "입력하신 종목을 찾지 못했어요.\n종목명을 다시 입력해 주세요!"
    return kakao_simple(text)


def stock_list_screen(user_name: str, list_type: str, stocks: List[str]):
    title = "관심 종목" if list_type == "watchlist" else "보유 종목"
    lines = "\n".join([f"- {s}" for s in stocks])
    text = (
        f"{user_name}님의 {title}은 다음과 같아요.\n\n"
        f"{lines}\n\n"
        "어떤 종목의 리포트를 확인할까요?\n"
        "종목명을 입력해 주세요!"
    )
    return kakao_simple(text)


def no_stocks_screen(user_name: str, list_type: str):
    if list_type == "holdings":
        text = (
            f"{user_name}님의 보유 종목이 없어요.\n\n"
            "보유 종목에 대한 종목 리포트를 확인하려면,\n"
            "종목을 매수 후 다시 이용해주세요.\n\n"
            "관심 종목이 있다면 하단의 관심 종목 버튼을 눌러\n"
            "종목 리포트를 확인하세요 !"
        )
    else:
        text = (
            f"{user_name}님의 관심 종목이 없어요.\n\n"
            "관심 종목이 있는 경우,\n"
            "해당 종목을 관심 종목으로 등록한 후\n"
            "다시 이용해주세요."
        )
    return kakao_simple(text)


# ---------------------------
# 챗봇 전용 엔드포인트
# ---------------------------

@router.post("/chatbot/report")
def chatbot_report(req: ChatbotReportRequest):
    mode = (req.mode or "").strip()
    ticker = (req.ticker or "").strip()
    section = (req.section or "").strip()
    user_name = (req.user_name or "사용자").strip() or "사용자"
    list_type = (req.list_type or "").strip()
    stocks = req.stocks or []

    bot = ChatbotStockReport()

    # entry
    if mode == "entry":
        return entry_screen()

    # 계좌 미연동 안내
    if mode == "account_required":
        return account_required_screen()

    # 종목 직접 입력 안내
    if mode == "stock_input_prompt":
        return stock_input_prompt_screen()

    # 종목 매칭 실패
    if mode == "stock_not_found":
        return stock_not_found_screen()

    # 관심/보유 종목 리스트
    if mode in ("watchlist", "holdings"):
        lt = "watchlist" if mode == "watchlist" else "holdings"
        if stocks:
            return stock_list_screen(user_name, lt, stocks)
        return no_stocks_screen(user_name, lt)

    # 요약
    if mode == "summary":
        if not ticker:
            return stock_input_prompt_screen()
        return bot.format_summary_for_kakao(ticker)

    # 주제 메뉴
    if mode == "topic_menu":
        return bot.format_topic_menu_for_kakao()

    # 섹션 상세
    if mode == "section":
        if not ticker or not section:
            raise HTTPException(status_code=400, detail="ticker and section required")
        return bot.format_section_for_kakao(ticker, section)

    # 전체 확인
    if mode == "all_sections":
        if not ticker:
            raise HTTPException(status_code=400, detail="ticker required")
        return bot.format_all_sections_for_kakao(ticker)

    raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")