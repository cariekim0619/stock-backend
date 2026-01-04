from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.report_service import generate_report


router = APIRouter(
    prefix="/api/stocks",
    tags=["Stock Reports"],
)


class ReportRequest(BaseModel):
    ticker: str


@router.post("/report")
def get_report(request: ReportRequest):
    """
    Lambda에서 호출하는 리포트 엔드포인트.

    - request body: {"ticker": "<종목명 또는 코드>"}
    - return: Kakao 스킬 JSON(dict)
      {
        "version": "2.0",
        "template": { ... }
      }
    """
    ticker = (request.ticker or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    print("[ec2][report] incoming ticker:", ticker)

    result = generate_report(ticker)

    # 방어: generate_report가 Kakao 스킬 포맷을 반드시 돌려주도록 했지만,
    # 혹시 타입이 어긋날 경우 500 에러로 명확히 표시
    if not isinstance(result, dict) or "version" not in result or "template" not in result:
        print("[ec2][report] invalid kakao skill format:", type(result), str(result)[:200])
        raise HTTPException(status_code=500, detail="invalid kakao skill format")

    return result
