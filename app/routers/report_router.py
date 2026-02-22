from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from app.services.report_service import generate_report
from app.services.chatbot_stock_report import ChatbotStockReport

router = APIRouter(
    prefix="/api/stocks",
    tags=["Stock Reports"],
)


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


class ChatbotReportRequest(BaseModel):
    mode: str
    ticker: Optional[str] = ""
    uuid: Optional[str] = ""
    section: Optional[str] = ""
    user_name: Optional[str] = "사용자"
    list_type: Optional[str] = ""
    stocks: Optional[List[str]] = None


@router.post("/chatbot/report")
def chatbot_report(req: ChatbotReportRequest):
    """
    Chatbot_02 종목리포트 기획안 전용 엔드포인트
    - mode 기반으로 '기획안 문구 + 버튼'을 서버에서 생성하여 반환
    """
    mode = (req.mode or "").strip()
    ticker = (req.ticker or "").strip()
    section = (req.section or "").strip()
    user_name = (req.user_name or "사용자").strip() or "사용자"
    list_type = (req.list_type or "").strip()
    stocks = req.stocks or []

    bot = ChatbotStockReport()

    if mode == "entry":
        return bot.entry()

    if mode == "account_required":
        return bot.account_required()

    if mode == "stock_input_prompt":
        return bot.stock_input_prompt()

    if mode == "stock_not_found":
        return bot.stock_not_found()

    if mode in ("watchlist", "holdings"):
        # 실제 연동 데이터는 서버 구현에 따라 uuid로 조회하거나, req.stocks로 받도록 구성
        if stocks:
            return bot.stock_list(user_name=user_name, list_type=("watchlist" if mode=="watchlist" else "holdings"), stocks=stocks)
        return bot.no_stocks(user_name=user_name, list_type=("watchlist" if mode=="watchlist" else "holdings"))

    if mode == "stock_list":
        if not list_type:
            raise HTTPException(status_code=400, detail="list_type is required for stock_list")
        return bot.stock_list(user_name=user_name, list_type=list_type, stocks=stocks)

    if mode == "no_stocks":
        if not list_type:
            raise HTTPException(status_code=400, detail="list_type is required for no_stocks")
        return bot.no_stocks(user_name=user_name, list_type=list_type)

    if mode == "summary":
        if not ticker:
            return bot.stock_input_prompt()
        return bot.summary(ticker)

    if mode == "topic_menu":
        return bot.topic_menu()

    if mode == "section":
        if not ticker or not section:
            raise HTTPException(status_code=400, detail="ticker and section are required for section")
        # section은 내부 키로 받는다(investment_summary/price_analysis/financial_analysis/valuation/investment_opinion)
        # 버튼에서 한국어로 들어오는 경우는 람다에서 매핑해서 넣는 것을 권장
        return bot.section(ticker, section)

    if mode == "all_sections":
        if not ticker:
            raise HTTPException(status_code=400, detail="ticker is required for all_sections")
        return bot.all_sections(ticker)

    raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")
