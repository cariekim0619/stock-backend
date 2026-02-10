def _normalize_ticker(ticker: str) -> str:
    """
    - '삼성전자' 같이 이름으로 들어와도
    - '005930' 같이 코드로 들어와도
    → 모두 6자리 코드로 정규화한다.
    """
    if not ticker:
        return ticker

    t = ticker.strip()

    # 1) 미리 정의한 이름 매핑 우선
    if t in NAME_TO_CODE:
        return NAME_TO_CODE[t]

    # 2) 이미 종목코드 형태면 바로 반환
    if t.isdigit() and len(t) == 6:
        return t

    # 3) 이름이면 FDR 종목 리스트에서 검색
    if fdr is not None:
        try:
            stocks = fdr.StockListing("KRX")
            row = stocks[stocks["Name"] == t]
            if not row.empty:
                return str(row.iloc[0]["Code"])
        except Exception as e:
            print("[_normalize_ticker] KRX 조회 실패:", e)

    # 못 찾으면 그대로 반환 (→ 나중에 데이터 없음 처리)
    return t
