from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.services.report_service import generate_report
from app.services.chatbot_report.chatbot_stock_report import ChatbotStockReport
from app.services.segment_personalization import apply_personalization_to_kakao, normalize_segment

# (선택) 티커 정규화 유틸이 있으면 사용
try:
    from app.utils.ticker_normalizer import (
        normalize_ticker,
        resolve_symbol_and_name,
        get_company_name_by_symbol,
    )
except Exception:
    normalize_ticker = None
    resolve_symbol_and_name = None
    get_company_name_by_symbol = None


router = APIRouter(
    prefix="/api/stocks",
    tags=["Stock Reports"],
)


# ---------------------------
# 1) 웹용 리포트 (기존 유지)
# ---------------------------

class ReportRequest(BaseModel):
    ticker: str
    segment: Optional[str] = "risk-neutral"
    profile: Optional[Dict[str, Any]] = None
    survey_profile: Optional[Dict[str, Any]] = None
    personalization: Optional[Dict[str, Any]] = None


@router.post("/report")
def get_report(request: ReportRequest):
    ticker = (request.ticker or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    segment = normalize_segment(request.segment)
    profile = request.profile or request.survey_profile
    result = generate_report(ticker, segment=segment, profile=profile)

    if not isinstance(result, dict) or "version" not in result or "template" not in result:
        raise HTTPException(status_code=500, detail="invalid kakao skill format")

    return result


# ---------------------------
# 2) 챗봇 전용 요청 모델
# ---------------------------

class ChatbotReportRequest(BaseModel):
    mode: str
    ticker: Optional[str] = ""
    uuid: Optional[str] = ""          # 현재 미사용
    section: Optional[str] = ""
    user_name: Optional[str] = "사용자"   # 현재 chatbot_stock_report.py에서는 실제 사용 안 함
    list_type: Optional[str] = ""
    stocks: Optional[List[str]] = None
    segment: Optional[str] = "risk-neutral"
    profile: Optional[Dict[str, Any]] = None
    survey_profile: Optional[Dict[str, Any]] = None
    personalization: Optional[Dict[str, Any]] = None


# ---------------------------
# 3) symbol / company_name / section 매핑 유틸
# ---------------------------


def _is_valid_symbol(symbol: str) -> bool:
    return bool(__import__("re").fullmatch(r"[0-9A-Z]{6}", (symbol or "").strip().upper()))

def _normalize_symbol(ticker: str) -> str:
    t = (ticker or "").strip()
    if not t:
        return ""

    if resolve_symbol_and_name:
        try:
            resolved = resolve_symbol_and_name(t)
            if resolved:
                return resolved[0]
            return ""
        except Exception:
            pass

    if normalize_ticker:
        try:
            normalized = (normalize_ticker(t) or "").strip().upper()
            if _is_valid_symbol(normalized):
                return normalized
        except Exception:
            pass

    return ""


def _resolve_company_name(bot: ChatbotStockReport, symbol: str, fallback: str) -> str:
    """
    company_name을 최대한 정확히 뽑는다.
    1) S3/캐시 역조회
    2) chart_provider.get_stock_info(symbol)에서 추출
    3) fallback
    """
    if get_company_name_by_symbol:
        try:
            cached_name = get_company_name_by_symbol(symbol)
            if isinstance(cached_name, str) and cached_name.strip():
                return cached_name.strip()
        except Exception:
            pass

    try:
        info = bot.chart_provider.get_stock_info(symbol)  # type: ignore[attr-defined]
        if isinstance(info, dict):
            candidates = [
                "name", "Name",
                "company_name", "company", "Company",
                "stock_name", "종목명",
            ]
            for key in candidates:
                v = info.get(key)
                if isinstance(v, str):
                    name = v.strip()
                    if name and not name.isdigit() and name != symbol:
                        return name
    except Exception:
        pass

    return fallback


def _normalize_section_key(bot: ChatbotStockReport, section: str) -> str:
    """
    section이 한국어 라벨이든 내부 key든 모두 허용한다.
    """
    s = (section or "").strip()
    if not s:
        return ""

    if s in bot.SECTIONS:
        return s

    inverse_map = {v: k for k, v in bot.SECTIONS.items()}
    if s in inverse_map:
        return inverse_map[s]

    s2 = s.replace(" ", "")
    inverse_map_no_space = {v.replace(" ", ""): k for k, v in bot.SECTIONS.items()}
    if s2 in inverse_map_no_space:
        return inverse_map_no_space[s2]

    return ""


def _to_bot_list_type(value: str) -> str:
    """
    chatbot_stock_report.py 기준으로 변환
    - favorite
    - holding

    라우터/외부 요청에서 watchlist, holdings가 들어와도 허용
    """
    mapping = {
        "watchlist": "favorite",
        "favorite": "favorite",
        "holdings": "holding",
        "holding": "holding",
    }
    return mapping.get((value or "").strip(), "")


# ---------------------------
# 4) 챗봇 전용 엔드포인트
# ---------------------------

@router.post("/chatbot/report")
def chatbot_report(req: ChatbotReportRequest):
    mode = (req.mode or "").strip()
    ticker = (req.ticker or "").strip()
    section = (req.section or "").strip()
    list_type = (req.list_type or "").strip()
    stocks = req.stocks or []
    segment = normalize_segment(req.segment)
    profile = req.profile or req.survey_profile

    bot = ChatbotStockReport()

    # ---- 고정 화면 ----
    if mode == "entry":
        return bot.format_entry_for_kakao()

    if mode == "account_required":
        return bot.format_no_account_for_kakao()

    if mode == "stock_input_prompt":
        return bot.format_input_prompt_for_kakao()

    if mode == "stock_not_found":
        return bot.format_stock_not_found_for_kakao()

    if mode == "stock_not_in_list":
        return bot.format_stock_not_in_list_for_kakao()

    # ---- 관심/보유 목록 화면 ----
    if mode in ("watchlist", "holdings"):
        bot_list_type = _to_bot_list_type(mode)

        if stocks:
            return bot.format_stock_list_for_kakao(stocks, bot_list_type)

        return bot.format_empty_list_for_kakao(bot_list_type)

    if mode == "stock_list":
        bot_list_type = _to_bot_list_type(list_type)
        if bot_list_type not in ("favorite", "holding"):
            raise HTTPException(
                status_code=400,
                detail="list_type must be one of: watchlist, holdings, favorite, holding",
            )
        return bot.format_stock_list_for_kakao(stocks, bot_list_type)

    if mode == "no_stocks":
        bot_list_type = _to_bot_list_type(list_type)
        if bot_list_type not in ("favorite", "holding"):
            raise HTTPException(
                status_code=400,
                detail="list_type must be one of: watchlist, holdings, favorite, holding",
            )
        return bot.format_empty_list_for_kakao(bot_list_type)

    # ---- summary ----
    if mode == "summary":
        if not ticker:
            return bot.format_input_prompt_for_kakao()

        symbol = _normalize_symbol(ticker)
        if not symbol:
            return bot.format_stock_not_found_for_kakao()

        company_name = _resolve_company_name(bot, symbol, fallback="종목")
        summary_dict = bot.get_report_summary(symbol, company_name, segment=segment, profile=profile)

        return apply_personalization_to_kakao(bot.format_summary_for_kakao(summary_dict), segment, domain="report")

    # ---- topic_menu ----
    if mode == "topic_menu":
        return bot.format_topic_menu_for_kakao()

    # ---- section ----
    if mode == "section":
        if not ticker:
            raise HTTPException(status_code=400, detail="ticker is required for section")

        symbol = _normalize_symbol(ticker)
        if not symbol:
            return bot.format_stock_not_found_for_kakao()

        section_key = _normalize_section_key(bot, section)
        if not section_key:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid section. "
                    f"use one of keys: {list(bot.SECTIONS.keys())} "
                    f"or labels: {list(bot.SECTIONS.values())}"
                ),
            )

        company_name = _resolve_company_name(bot, symbol, fallback="종목")
        detail_dict = bot.get_section_detail(
            symbol=symbol,
            company_name=company_name,
            section=section_key,
            raw_data=None,
            segment=segment,
            profile=profile,
        )
        return apply_personalization_to_kakao(bot.format_section_for_kakao(detail_dict), segment, domain="report")

    # ---- all_sections ----
    if mode == "all_sections":
        if not ticker:
            raise HTTPException(status_code=400, detail="ticker is required for all_sections")

        symbol = _normalize_symbol(ticker)
        if not symbol:
            return bot.format_stock_not_found_for_kakao()

        company_name = _resolve_company_name(bot, symbol, fallback="종목")
        all_sections_dict = bot.get_all_sections(symbol, company_name, segment=segment, profile=profile)

        return apply_personalization_to_kakao(bot.format_all_sections_for_kakao(all_sections_dict), segment, domain="report")

    raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")