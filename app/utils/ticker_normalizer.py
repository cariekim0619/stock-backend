# app/utils/ticker_normalizer.py
"""
입력으로 들어온 '종목명/종목코드/별칭'을 KRX 6자리 종목코드로 정규화합니다.
- FinanceDataReader(=fdr)가 설치되어 있으면 KRX 리스트를 조회해 종목명→코드 변환
- fdr가 없거나 조회 실패 시에는 최소한의 하드코딩 매핑으로 fallback
- KRX 리스트 조회는 TTL 캐시를 사용합니다.
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

try:
    import FinanceDataReader as fdr  # type: ignore
except Exception:
    fdr = None

# 최소 별칭 매핑(필요시 계속 추가)
NAME_TO_CODE = {
    "삼성전자": "005930",
    "카카오": "035720",
    "현대차": "005380",
    "LG에너지솔루션": "373220",
    "SK하이닉스": "000660",
    "네이버": "035420",
    "NAVER": "035420",
    "POSCO홀딩스": "005490",
    "포스코": "005490",
    "기아": "000270",
    "셀트리온": "068270",
    "삼성바이오로직스": "207940",
}

_KRX_CACHE_DF = None
_KRX_CACHE_TS = 0.0
_KRX_CACHE_TTL = int(os.getenv("KRX_LISTING_CACHE_TTL_SEC", "3600"))

def _get_krx_listing():
    global _KRX_CACHE_DF, _KRX_CACHE_TS
    if fdr is None:
        return None

    now = time.time()
    if _KRX_CACHE_DF is not None and (now - _KRX_CACHE_TS) < _KRX_CACHE_TTL:
        return _KRX_CACHE_DF

    try:
        _KRX_CACHE_DF = fdr.StockListing("KRX")
        _KRX_CACHE_TS = now
        return _KRX_CACHE_DF
    except Exception:
        return _KRX_CACHE_DF  # 마지막 성공 캐시라도 반환

def normalize_ticker(ticker: str) -> str:
    if ticker is None:
        return ""
    t = str(ticker).strip()
    if not t:
        return ""

    if t in NAME_TO_CODE:
        return NAME_TO_CODE[t]

    if t.isdigit():
        if len(t) == 6:
            return t
        if len(t) < 6:
            return t.zfill(6)

    df = _get_krx_listing()
    if df is not None:
        try:
            m = df[df["Name"].astype(str) == t]
            if not m.empty:
                return str(m.iloc[0]["Code"]).zfill(6)

            t_ns = t.replace(" ", "")
            m2 = df[df["Name"].astype(str).str.replace(" ", "", regex=False) == t_ns]
            if not m2.empty:
                return str(m2.iloc[0]["Code"]).zfill(6)
        except Exception:
            pass

    return t

def resolve_symbol_and_name(ticker: str) -> Tuple[str, str]:
    raw = ("" if ticker is None else str(ticker)).strip()
    symbol = normalize_ticker(raw)

    company_name = raw

    if raw in NAME_TO_CODE:
        company_name = raw
        return symbol, company_name

    if symbol and symbol.isdigit() and len(symbol) == 6:
        df = _get_krx_listing()
        if df is not None:
            try:
                codes = df["Code"].astype(str).str.zfill(6)
                m = df[codes == symbol]
                if not m.empty:
                    company_name = str(m.iloc[0]["Name"]) or company_name
            except Exception:
                pass
        else:
            for n, c in NAME_TO_CODE.items():
                if c == symbol:
                    company_name = n
                    break

    return symbol, company_name

