from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import os

# -------------------------------------------------------------------
# 0) FinanceDataReader (실시간/준실시간 주가용)
# -------------------------------------------------------------------
try:
    import FinanceDataReader as fdr
except Exception:
    fdr = None

# -------------------------------------------------------------------
# 0-1) DART / 재무제표 기반 지표 계산 모듈
#   - app/clients/dart_client.py
#   - app/domain/dart_financial_loader.py
#   - app/domain/metrics_calculator.py
# -------------------------------------------------------------------
try:
    from app.clients.dart_client import DartClient
    from app.domain.dart_financial_loader import DartFinancialLoader
    from app.domain.metrics_calculator import MetricsCalculator
except Exception as e:
    print("[raw_report_service] DART 모듈 import 실패:", e)
    DartClient = None
    DartFinancialLoader = None
    MetricsCalculator = None

DART_LOADER: Optional["DartFinancialLoader"] = None
METRICS_CALCULATOR: Optional["MetricsCalculator"] = None

if DartClient and DartFinancialLoader and MetricsCalculator:
    dart_key = os.environ.get("DART_API_KEY")
    if dart_key:
        try:
            dart_client = DartClient(api_key=dart_key)
            DART_LOADER = DartFinancialLoader(dart_client)
            METRICS_CALCULATOR = MetricsCalculator()
            print("[raw_report_service] ✅  DART 모듈 초기화 완료")
        except Exception as e:
            print("[raw_report_service] ⚠️ DART 초기화 실패:", e)
    else:
        print("[raw_report_service] ⚠️ DART_API_KEY 미설정")

# -------------------------------------------------------------------
# 1) 최소한의 종목명 -> 코드 매핑 (자주 쓰는 것만 하드코딩)
# -------------------------------------------------------------------
NAME_TO_CODE: Dict[str, str] = {
    "삼성전자": "005930",
    "카카오": "035720",
    "LG에너지솔루션": "373220",
}

# -------------------------------------------------------------------
# 2) 공통 유틸: 티커 정규화 / 포맷터
# -------------------------------------------------------------------
def _normalize_ticker(ticker: str) -> str:
    """
    - '삼성전자' 같이 이름으로 들어와도
    - '005930' 같이 코드로 들어와도
    → 모두 6자리 코드로 정규화
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


def _fmt_pct(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return "N/A"
    return f"{v:+.2f}%"


def _fmt_won(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return "N/A"
    return f"{v:,.0f}원"


# -------------------------------------------------------------------
# 3) 실시간 스냅샷: 가격/수익률/기본 정보 (FDR)
# -------------------------------------------------------------------
def load_stock_snapshot(ticker: str) -> Optional[Dict[str, Any]]:
    code = _normalize_ticker(ticker)

    if fdr is None:
        print("[load_stock_snapshot] FinanceDataReader 미설치 → None 반환")
        return None

    # 1) 종목 리스트 (이름, 시가총액, 시총 순위 계산용)
    try:
        stocks = fdr.StockListing("KRX")
    except Exception as e:
        print(f"[load_stock_snapshot] StockListing 조회 오류: {e}")
        return None

    row_df = stocks[stocks["Code"] == code]
    if row_df.empty:
        row_df = stocks[stocks["Name"] == ticker]
        if row_df.empty:
            print(f"[load_stock_snapshot] 종목 리스트에서 {ticker} 를 찾지 못함")
            return None

    row = row_df.iloc[0]

    name = str(row.get("Name", code))

    # 시가총액
    marcap_val = row.get("Marcap")
    try:
        market_cap = int(marcap_val) if marcap_val is not None else None
    except Exception:
        market_cap = None

    # 시총 순위
    try:
        stocks_sorted = stocks.sort_values("Marcap", ascending=False).reset_index(drop=True)
        idx = stocks_sorted[stocks_sorted["Code"] == code].index
        market_cap_rank = int(idx[0] + 1) if len(idx) > 0 else None
    except Exception:
        market_cap_rank = None

    # 2) 가격/수익률 계산용 1년 데이터
    try:
        end = datetime.today()
        start = end - timedelta(days=365)
        df = fdr.DataReader(code, start, end)
    except Exception as e:
        print(f"[load_stock_snapshot] DataReader 조회 오류: {e}")
        return None

    if df is None or df.empty:
        print(f"[load_stock_snapshot] 일봉 데이터 없음 (code={code})")
        return None

    for col in ["Close", "High", "Low"]:
        if col not in df.columns:
            print(f"[load_stock_snapshot] 컬럼 {col} 없음 (code={code})")
            return None

    df = df.dropna(subset=["Close"])
    if df.empty:
        print(f"[load_stock_snapshot] Close 전부 NaN (code={code})")
        return None

    current_price = float(df["Close"].iloc[-1])
    high_52w = float(df["High"].max())
    low_52w = float(df["Low"].min())

    def pct_from_n_days(n: int) -> Optional[float]:
        if len(df) <= n:
            return None
        past_price = float(df["Close"].iloc[-(n + 1)])
        if past_price <= 0:
            return None
        return (current_price / past_price - 1.0) * 100.0

    ret_1m = pct_from_n_days(20)
    ret_3m = pct_from_n_days(60)
    ret_1y = pct_from_n_days(240)

    from_high: Optional[float] = None
    if high_52w > 0:
        from_high = (current_price / high_52w - 1.0) * 100.0

    snapshot: Dict[str, Any] = {
        "ticker": code,
        "name": name,
        "current_price": int(current_price),
        "market_cap": market_cap,
        "market_cap_rank": market_cap_rank,
        "ret_1m": ret_1m,
        "ret_3m": ret_3m,
        "ret_1y": ret_1y,
        "high_52w": int(high_52w),
        "low_52w": int(low_52w),
        "from_high": from_high,
    }

    return snapshot


# -------------------------------------------------------------------
# 4) 1차 밸류에이션 지표: FDR StockSummary (있으면 사용)
# -------------------------------------------------------------------
def load_stock_metrics(ticker: str) -> Dict[str, Any]:
    """
    1차: FDR StockSummary
    - 없거나 에러면 키만 만들고 None으로 채움
    - 이후 DART 보강 단계에서 실제 값 계산
    """
    code = _normalize_ticker(ticker)

    if fdr is not None and hasattr(fdr, "StockSummary"):
        try:
            summary = fdr.StockSummary(code)
            if summary is not None:
                return {
                    "per": summary.get("PER"),
                    "pbr": summary.get("PBR"),
                    "roe": summary.get("ROE"),
                    "eps": summary.get("EPS"),
                    "bps": summary.get("BPS"),
                }
        except Exception as e:
            print(f"[load_stock_metrics] FDR StockSummary 조회 오류: {e}")

    # 여기까지 왔으면 FDR에서는 값을 못 얻은 상태
    return {
        "per": None,
        "pbr": None,
        "roe": None,
        "eps": None,
        "bps": None,
    }


# -------------------------------------------------------------------
# 5) FDR metrics 부족 시 DART로 보강
# -------------------------------------------------------------------
def _enhance_metrics_with_dart_if_needed(
    ticker: str,
    metrics: Dict[str, Any],
    current_price: Optional[float],
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    FDR 기반 metrics가 모두 None이면
    → DART 재무제표로 PER·PBR·ROE·EPS·BPS 재계산
    """
    if not (DART_LOADER and METRICS_CALCULATOR and current_price):
        return metrics, None

    values = [metrics.get(k) for k in ("per", "pbr", "roe", "eps", "bps")]
    has_any_value = any(v is not None for v in values)

    if has_any_value:
        # FDR에서라도 값이 하나라도 있으면 그걸 우선 사용
        return metrics, None

    code = _normalize_ticker(ticker)
    print(f"[DART] FDR metrics 없음 → DART 재무제표로 재계산 시도 (ticker={code})")

    # 1) DART 재무제표 로딩
    try:
        financial_text, financial_df = DART_LOADER.load_financials(code)
    except Exception as e:
        print(f"[DART] 재무제표 로딩 실패: {e}")
        return metrics, None

    if financial_df is None or financial_df.empty:
        print("[DART] 재무제표 DataFrame 비어 있음")
        return metrics, None

    # 2) 재무제표 기반 지표 계산
    calculated = METRICS_CALCULATOR.calculate_from_dataframe(
        financial_df,
        current_price=current_price,
    )

    if not calculated:
        print("[DART] MetricsCalculator 결과 없음")
        return metrics, financial_text

    # 3) PER·PBR·ROE·EPS·BPS 덮어쓰기
    for key in ("per", "pbr", "roe", "eps", "bps"):
        if key in calculated and calculated[key] is not None:
            metrics[key] = calculated[key]

    print(
        f"[DART] 밸류 지표 업데이트 → "
        f"PER={metrics.get('per')}, PBR={metrics.get('pbr')}, ROE={metrics.get('roe')}"
    )

    return metrics, financial_text


# -------------------------------------------------------------------
# 6) 최종 리포트 조립
# -------------------------------------------------------------------
def generate_raw_report(ticker: str) -> Dict[str, Any]:
    """
    최종: 내부 리포트 JSON 생성
    - 스냅샷(FDR)
    - 밸류에이션(FDR → DART 보강)
    """
    # 타입 가드: 혹시 dict 같은 게 넘어오면 바로 no-data 처리
    if not isinstance(ticker, str):
        print(f"[generate_raw_report] invalid ticker type: {type(ticker)} {repr(ticker)[:200]}")
        return {}

    ticker = ticker.strip()
    if not ticker:
        print("[generate_raw_report] empty ticker")
        return {}

    snapshot = load_stock_snapshot(ticker)
    if not snapshot:
        print(f"[generate_raw_report] snapshot not found for ticker={ticker!r}")
        return {}

    # 1차: FDR
    metrics = load_stock_metrics(ticker) or {}

    name = snapshot.get("name", ticker)
    current_price = snapshot.get("current_price")
    market_cap = snapshot.get("market_cap")
    market_cap_rank = snapshot.get("market_cap_rank")

    ret_1m = snapshot.get("ret_1m")
    ret_3m = snapshot.get("ret_3m")
    ret_1y = snapshot.get("ret_1y")
    high_52w = snapshot.get("high_52w")
    low_52w = snapshot.get("low_52w")
    from_high = snapshot.get("from_high")

    # 2차: DART 보강
    financial_text: Optional[str] = None
    try:
        metrics, financial_text = _enhance_metrics_with_dart_if_needed(
            ticker=snapshot.get("ticker") or _normalize_ticker(ticker),
            metrics=metrics,
            current_price=current_price,
        )
    except Exception as e:
        print(f"[DART] metrics 보강 중 예외 발생: {e}")

    per = metrics.get("per")
    pbr = metrics.get("pbr")
    roe = metrics.get("roe")
    eps = metrics.get("eps")
    bps = metrics.get("bps")

    raw_data: Dict[str, Any] = {
        "basic": {
            "ticker": snapshot.get("ticker") or _normalize_ticker(ticker),
            "name": name,
            "current_price": current_price,
            "market_cap": market_cap,
            "market_cap_rank": market_cap_rank,
        },
        "price_trend": {
            "1m": ret_1m,
            "3m": ret_3m,
            "1y": ret_1y,
            "52w_high": high_52w,
            "52w_low": low_52w,
            "from_high": from_high,
        },
        "metrics": {
            "per": per,
            "pbr": pbr,
            "roe": roe,
            "eps": eps,
            "bps": bps,
        },
        "technical": {
            "rsi": None,
            "rsi_signal": "N/A",
        },
    }

    if financial_text:
        raw_data["financial_text"] = financial_text

    summary_text = (
        f"{name}의 현재 주가는 {_fmt_won(current_price)}입니다. "
        f"최근 1년 수익률은 {_fmt_pct(ret_1y)} 수준입니다."
    )

    price_analysis_text = (
        f"최근 1개월 수익률은 {_fmt_pct(ret_1m)}, "
        f"3개월 수익률은 {_fmt_pct(ret_3m)}, "
        f"1년 수익률은 {_fmt_pct(ret_1y)}입니다. "
        f"52주 고점은 {_fmt_won(high_52w)}, "
        f"52주 저점은 {_fmt_won(low_52w)}이며, "
        f"현재가는 52주 고점 대비 {_fmt_pct(from_high)} 위치에 있습니다."
    )

    financial_analysis_text = (
        "재무제표(매출, 영업이익, 순이익 등)에 대한 상세 분석은 "
        "향후 DART 재무제표 데이터를 연동해 확장할 수 있습니다."
    )

    valuation_text = (
        "PER·PBR·ROE와 같은 밸류에이션 지표를 기반으로 "
        "현재 주가의 상대적인 수준을 평가할 수 있습니다. "
        f"현재 PER은 {per if per is not None else 'N/A'}, "
        f"PBR은 {pbr if pbr is not None else 'N/A'}, "
        f"ROE는 {roe if roe is not None else 'N/A'} 입니다."
    )

    investment_opinion_text = (
        "본 리포트는 참고용 정보이며, 개별 투자자의 위험 성향과 투자 기간을 "
        "함께 고려해 최종 판단을 내리는 것이 좋습니다. "
        "구체적인 매수·매도 의견과 목표 주가는 별도로 제시하지 않습니다."
    )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_data: Dict[str, Any] = {
        "ticker": snapshot.get("ticker") or _normalize_ticker(ticker),
        "name": name,
        "generated_at": generated_at,
        "report": {
            "title": f"{name} 투자 리포트",
            "full_text": "",
            "sections": {
                "summary": summary_text,
                "price_analysis": price_analysis_text,
                "financial_analysis": financial_analysis_text,
                "valuation": valuation_text,
                "investment_opinion": investment_opinion_text,
            },
            "has_financials": bool(metrics),
        },
        "raw_data": raw_data,
    }

    return report_data
