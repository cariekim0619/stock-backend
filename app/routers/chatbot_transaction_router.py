from fastapi import APIRouter
from app.services.chatbot_transaction.chatbot_transaction_report import ChatbotTransactionReport

router = APIRouter(prefix="/api/chatbot/transaction", tags=["chatbot-transaction"])

service = ChatbotTransactionReport()

@router.post("/report")
async def get_transaction_report(req: dict):
    """
    카카오 챗봇 → 거래내역 리포트 API
    """

    # 1. 입력 파싱
    symbol = req.get("symbol")
    company = req.get("company_name")

    # 2. 계좌 연동 여부 체크 (여기 중요)
    is_linked = req.get("is_account_linked", False)

    if not is_linked:
        return service.format_account_not_linked_kakao()

    # 3. 종목 없음
    if not symbol:
        return service.format_stock_not_found_for_kakao()

    # 4. 리포트 생성
    report = service.get_transaction_report(symbol, company)

    # 5. 카카오 포맷 변환
    return service.format_report_for_kakao(report)