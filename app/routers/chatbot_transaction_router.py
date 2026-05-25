from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.chatbot_transaction.chatbot_transaction_report import ChatbotTransactionReport

router = APIRouter(prefix="/api/chatbot/transaction", tags=["chatbot-transaction"])

service = ChatbotTransactionReport()


class KisCredentials(BaseModel):
    env: Optional[str] = None
    account_no: Optional[str] = None
    account_id: Optional[str] = None
    account_suffix: Optional[str] = None
    appkey: Optional[str] = None
    appsecret: Optional[str] = None
    access_token: Optional[str] = None


class TransactionReportRequest(BaseModel):
    symbol: Optional[str] = ""
    company_name: Optional[str] = ""
    is_account_linked: bool = False
    period: str = "1m"
    segment: str = "risk-neutral"
    profile: Optional[dict] = None
    kis: Optional[KisCredentials] = None


@router.post("/report")
async def get_transaction_report(req: TransactionReportRequest):
    """카카오 챗봇 → 거래내역 리포트 API"""
    try:
        if not req.is_account_linked:
            return service.format_account_not_linked_kakao()
        if not req.symbol:
            return service.format_stock_not_found_for_kakao()
        hantu_override = None
        if req.kis and req.kis.appkey and req.kis.appsecret:
            from app.services.chatbot_report.HantuStock import HantuStock

            account_no = (req.kis.account_no or "").strip()
            account_id = (req.kis.account_id or account_no or "").strip()
            hantu_override = HantuStock(
                api_key=req.kis.appkey,
                secret_key=req.kis.appsecret,
                account_id=account_id,
                account_suffix=req.kis.account_suffix,
                env=req.kis.env,
                access_token=req.kis.access_token,
            )

        report = service.get_transaction_report(
            symbol=req.symbol,
            company_name=req.company_name or req.symbol,
            period=req.period,
            segment=req.segment,
            profile=req.profile,
            hantu_override=hantu_override,
        )
        return service.format_report_for_kakao(report)
    except Exception as e:
        print(f"[ERROR] transaction/report failed: {type(e).__name__}: {e}")
        return service._kakao_error_response("거래내역 조회 중 문제가 발생했어요.")
