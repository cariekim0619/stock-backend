import os
import json
import datetime as dt
from typing import Optional, Dict, Any

import boto3

S3_BUCKET = os.getenv("STOCK_REPORT_S3_BUCKET", "stock-report-cache")
S3 = boto3.client("s3")

# 티커별 캐시 키: reports/by_ticker/005930.json 이런 식
def _ticker_key(ticker: str) -> str:
    return f"reports/by_ticker/{ticker}.json"


def load_cached_report(ticker: str, max_age_days: int = 3) -> Optional[Dict[str, Any]]:
    """
    S3에서 티커별 캐시된 리포트를 불러온다.
    max_age_days 이내면 유효, 그 이상이면 None 리턴.
    """
    key = _ticker_key(ticker)

    try:
        obj = S3.get_object(Bucket=S3_BUCKET, Key=key)
        body = obj["Body"].read().decode("utf-8")
        payload = json.loads(body)

        updated_at = payload.get("updated_at")
        if not updated_at:
            return None

        updated_dt = dt.datetime.fromisoformat(updated_at)
        if (dt.datetime.utcnow() - updated_dt).days > max_age_days:
            # 너무 오래된 데이터면 재계산 유도
            return None

        # 실제 리포트는 payload["report"] 안에 넣어두자
        return payload.get("report")

    except Exception:
        return None


def save_cached_report(ticker: str, report: Dict[str, Any]) -> None:
    key = _ticker_key(ticker)

    payload = {
        "ticker": ticker,
        "updated_at": dt.datetime.utcnow().isoformat(),
        "report": report,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    S3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )

