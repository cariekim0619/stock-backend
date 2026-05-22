from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.chatbot_transaction.chatbot_transaction_report import ChatbotTransactionReport

router = APIRouter(prefix="/api/chatbot/transaction", tags=["chatbot-transaction"])

service = ChatbotTransactionReport()


class TransactionReportRequest(BaseModel):
    symbol: Optional[str] = ""
    company_name: Optional[str] = ""
    is_account_linked: bool = False
    period: str = "1m"
    segment: str = "risk-neutral"
    profile: Optional[dict] = None


@router.post("/report")
async def get_transaction_report(req: TransactionReportRequest):
    """카카오 챗봇 → 거래내역 리포트 API"""
    try:
        if not req.is_account_linked:
            return service.format_account_not_linked_kakao()
        if not req.symbol:
            return service.format_stock_not_found_for_kakao()
        report = service.get_transaction_report(
            symbol=req.symbol,
            company_name=req.company_name or req.symbol,
            period=req.period,
            segment=req.segment,
            profile=req.profile,
        )
        return service.format_report_for_kakao(report)
    except Exception as e:
        print(f"[ERROR] transaction/report failed: {type(e).__name__}: {e}")
        return service._kakao_error_response("거래내역 조회 중 문제가 발생했어요.")
