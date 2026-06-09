from typing import Optional, Dict, Any, List

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.chatbot_transaction.chatbot_transaction_report import ChatbotTransactionReport

router = APIRouter(prefix="/api/chatbot/transaction", tags=["chatbot-transaction"])

service = ChatbotTransactionReport()


class TransactionReportRequest(BaseModel):
    symbol: Optional[str] = ""
    company_name: Optional[str] = ""
    requested_name: Optional[str] = ""
    company_aliases: Optional[List[str]] = None
    is_account_linked: bool = False
    period: str = "1m"
    segment: str = "risk-neutral"
    profile: Optional[Dict[str, Any]] = None
    survey_profile: Optional[Dict[str, Any]] = None
    personalization: Optional[Dict[str, Any]] = None

    # Per-user KIS credentials supplied by Lambda after 주식계좌 자동연동.
    # These override EC2 .env KIS_TRANSACTION_* values for this request only.
    kis_env: Optional[str] = None                 # real | paper | prod | vps
    kis_appkey: Optional[str] = None
    kis_appsecret: Optional[str] = None
    kis_app_key: Optional[str] = None             # compatibility alias
    kis_app_secret: Optional[str] = None          # compatibility alias
    kis_account_no: Optional[str] = None
    kis_account_id: Optional[str] = None          # compatibility alias
    kis_account_suffix: Optional[str] = None
    kis_account_product_code: Optional[str] = None


def _normalize_env(env: Optional[str]) -> str:
    raw = (env or "").strip().lower()
    if raw in {"paper", "vps", "demo", "sandbox", "vts"}:
        return "vps"
    if raw in {"real", "prod", "production"}:
        return "prod"
    return "prod"


def _request_hantu(req: TransactionReportRequest):
    """Build a per-request HantuStock instance from Lambda-supplied user credentials.

    If credentials are incomplete, return None so the service can keep its legacy
    .env fallback. A fresh ChatbotTransactionReport is used per request to avoid
    leaking one user's HantuStock instance through the module-level singleton.
    """
    appkey = (req.kis_appkey or req.kis_app_key or "").strip()
    appsecret = (req.kis_appsecret or req.kis_app_secret or "").strip()
    account_no = (req.kis_account_no or req.kis_account_id or "").strip()
    account_no = "".join(ch for ch in account_no if ch.isdigit())
    suffix = (req.kis_account_suffix or req.kis_account_product_code or "01").strip() or "01"

    if len(account_no) >= 10 and not (req.kis_account_suffix or req.kis_account_product_code):
        suffix = account_no[-2:]
        account_no = account_no[:-2]

    if not (appkey and appsecret and account_no):
        return None

    from app.services.chatbot_report.HantuStock import HantuStock
    return HantuStock(
        api_key=appkey,
        secret_key=appsecret,
        account_id=account_no,
        account_suffix=suffix,
        env=_normalize_env(req.kis_env),
    )


@router.post("/report")
async def get_transaction_report(req: TransactionReportRequest):
    """카카오 챗봇 → 거래내역 리포트 API"""
    local_service = ChatbotTransactionReport()
    try:
        if not req.is_account_linked:
            return local_service.format_account_not_linked_kakao()

        per_user_hantu = _request_hantu(req)
        if per_user_hantu is not None:
            local_service.hantu = per_user_hantu

        report = local_service.get_transaction_report(
            symbol=req.symbol or "",
            company_name=req.company_name or req.symbol or "전체 거래내역",
            requested_name=req.requested_name or req.company_name or "",
            company_aliases=req.company_aliases or [],
            period=req.period,
            segment=req.segment,
            profile=req.profile or req.survey_profile,
        )
        return local_service.format_report_for_kakao(report)
    except Exception as e:
        print(f"[ERROR] transaction/report failed: {type(e).__name__}: {e}")
        return local_service._kakao_error_response("거래내역 조회 중 문제가 발생했어요.")
