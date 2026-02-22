from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.services.report_service import generate_report
from app.services.chatbot_report.chatbot_stock_report import ChatbotStockReport

# (선택) 티커 정규화 유틸이 있으면 사용
try:
    from app.utils.ticker_normalizer import normalize_ticker
except Exception:
    normalize_ticker = None


router = APIRouter(
    prefix="/api/stocks",
    tags=["Stock Reports"],
)


# ---------------------------
# 1) 웹용 리포트 (기존 유지)
# ---------------------------

class ReportRequest(BaseModel):
    ticker: str


@router.post("/report")
def get_report(request: ReportRequest):
    ticker = (request.ticker or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    result = generate_report(ticker)

    if not isinstance(result, dict) or "version" not in result or "template" not in result:
        raise HTTPException(status_code=500, detail="invalid kakao skill format")

    return result


# ---------------------------
# 2) 챗봇 전용 요청 모델
# ---------------------------

class ChatbotReportRequest(BaseModel):
    mode: str
    ticker: Optional[str] = ""
    uuid: Optional[str] = ""             # 현재는 사용 안 해도 됨
    section: Optional[str] = ""
    user_name: Optional[str] = "사용자"  # watchlist/holdings 출력용
    list_type: Optional[str] = ""        # stock_list/no_stocks용
    stocks: Optional[List[str]] = None   # watchlist/holdings 목록을 서버가 받는 방식이면 사용


# ---------------------------
# 3) Kakao simpleText 헬퍼 (고정 화면용)
# ---------------------------

def _kakao_simple(text: str, quicks: Optional[List[dict]] = None) -> dict:
    payload = {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}]
        }
    }
    if quicks:
        payload["template"]["quickReplies"] = quicks
    return payload


def _entry_screen() -> dict:
    text = (
        "⬛️ 종목 리포트 기능에 대해 알려드릴게요\n\n"
        "➊ 관심 있는 종목의 투자 리포트를 확인할 수 있어요\n"
        "➋ 종목명을 직접 입력해서 바로 리포트를 볼 수도 있어요\n"
        "➌ 보유 종목과 관심 종목은 계좌가 연동되어 있을 때 확인할 수 있어요\n\n"
        "아래 버튼을 눌러 원하는 방법을 선택해 주세요!"
    )
    quicks = [
        {"action": "message", "label": "관심 종목 확인하기", "messageText": "관심 종목 확인하기"},
        {"action": "message", "label": "보유 종목 확인하기", "messageText": "보유 종목 확인하기"},
        {"action": "message", "label": "종목 직접 입력", "messageText": "종목 직접 입력"},
        {"action": "message", "label": "종목 리포트 종료", "messageText": "종목 리포트 종료"},
    ]
    return _kakao_simple(text, quicks)


def _account_required_screen() -> dict:
    text = (
        "해당 기능은 계좌 연결 후 이용할 수 있습니다 :)\n\n"
        "계좌를 연결하시면,\n"
        "보유 종목과 관심 종목의 리포트를 바로 확인할 수 있어요!\n\n"
        "💡 계좌를 연결하지 않아도\n"
        "종목명을 직접 입력하면 리포트를 확인할 수 있어요 :D"
    )
    quicks = [
        {"action": "message", "label": "계좌 연결하기", "messageText": "주식계좌연결"},
        {"action": "message", "label": "종목 직접 입력", "messageText": "종목 직접 입력"},
        {"action": "message", "label": "이전으로", "messageText": "이전으로"},
    ]
    return _kakao_simple(text, quicks)


def _stock_input_prompt_screen() -> dict:
    return _kakao_simple("종목명 또는 종목 코드를 입력해 주세요!")


def _stock_not_found_screen() -> dict:
    return _kakao_simple("입력하신 종목을 찾지 못했어요.\n종목명을 다시 입력해 주세요!")


def _stock_list_screen(user_name: str, list_type: str, stocks: List[str]) -> dict:
    title = "관심 종목" if list_type == "watchlist" else "보유 종목"
    lines = "\n".join([f"- {s}" for s in stocks]) if stocks else ""
    text = (
        f"{user_name}님의 {title}은 다음과 같아요.\n\n"
        f"{lines}\n\n"
        "어떤 종목의 리포트를 확인할까요?\n"
        "종목명을 입력해 주세요!"
    )
    return _kakao_simple(text)


def _no_stocks_screen(user_name: str, list_type: str) -> dict:
    if list_type == "holdings":
        text = (
            f"{user_name}님의 보유 종목이 없어요.\n\n"
            "보유 종목에 대한 종목 리포트를 확인하려면,\n"
            "종목을 매수 후 다시 이용해주세요.\n\n"
            "관심 종목이 있다면 하단의 관심 종목 버튼을 눌러\n"
            "종목 리포트를 확인하세요 !"
        )
        quicks = [
            {"action": "message", "label": "관심 종목 확인하기", "messageText": "관심 종목 확인하기"},
            {"action": "message", "label": "종목 직접 입력", "messageText": "종목 직접 입력"},
            {"action": "message", "label": "메인으로", "messageText": "메인으로"},
        ]
        return _kakao_simple(text, quicks)

    # watchlist
    text = (
        f"{user_name}님의 관심 종목이 없어요.\n\n"
        "관심 종목이 있는 경우,\n"
        "해당 종목을 관심 종목으로 등록한 후\n"
        "다시 이용해주세요."
    )
    quicks = [
        {"action": "message", "label": "보유 종목 확인하기", "messageText": "보유 종목 확인하기"},
        {"action": "message", "label": "종목 직접 입력", "messageText": "종목 직접 입력"},
        {"action": "message", "label": "메인으로", "messageText": "메인으로"},
    ]
    return _kakao_simple(text, quicks)


# ---------------------------
# 4) symbol / company_name / section 매핑 유틸
# ---------------------------

def _normalize_symbol(ticker: str) -> str:
    t = (ticker or "").strip()
    if not t:
        return ""
    if normalize_ticker:
        try:
            return normalize_ticker(t)
        except Exception:
            return t
    return t


def _resolve_company_name(bot: ChatbotStockReport, symbol: str, fallback: str) -> str:
    """
    get_report_summary(symbol, company_name)에서 company_name은 프롬프트/메시지에 쓰임
    - 가능하면 chart_provider.get_stock_info(symbol)에서 이름을 뽑는다
    """
    try:
        info = bot.chart_provider.get_stock_info(symbol)  # type: ignore[attr-defined]
        if isinstance(info, dict):
            # 프로젝트마다 키가 다를 수 있어 여러 후보를 확인
            for key in ("name", "company_name", "company", "종목명"):
                v = info.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return fallback


def _normalize_section_key(bot: ChatbotStockReport, section: str) -> str:
    """
    section이 한국어(예: '재무 분석')로 들어오거나,
    내부 키(예: 'financial_analysis')로 들어오는 걸 모두 처리
    """
    s = (section or "").strip()
    if not s:
        return ""

    # 내부 키로 이미 들어온 경우
    if s in bot.SECTIONS:
        return s

    # 한국어 라벨로 들어온 경우 -> 역매핑
    inv = {v: k for k, v in bot.SECTIONS.items()}
    if s in inv:
        return inv[s]

    # 혹시 '투자 요약' 같은 공백/변형 케이스 처리
    s2 = s.replace(" ", "")
    inv2 = {v.replace(" ", ""): k for k, v in bot.SECTIONS.items()}
    if s2 in inv2:
        return inv2[s2]

    return ""


# ---------------------------
# 5) 챗봇 전용 엔드포인트 (정석 연결)
# ---------------------------

@router.post("/chatbot/report")
def chatbot_report(req: ChatbotReportRequest):
    mode = (req.mode or "").strip()
    ticker = (req.ticker or "").strip()
    section = (req.section or "").strip()
    user_name = (req.user_name or "사용자").strip() or "사용자"
    list_type = (req.list_type or "").strip()
    stocks = req.stocks or []

    bot = ChatbotStockReport()

    # ---- 고정 화면: entry / 안내 ----
    if mode == "entry":
        return _entry_screen()

    if mode == "account_required":
        return _account_required_screen()

    if mode == "stock_input_prompt":
        return _stock_input_prompt_screen()

    if mode == "stock_not_found":
        return _stock_not_found_screen()

    # ---- 관심/보유 목록 화면 ----
    if mode in ("watchlist", "holdings"):
        lt = "watchlist" if mode == "watchlist" else "holdings"
        if stocks:
            return _stock_list_screen(user_name=user_name, list_type=lt, stocks=stocks)
        return _no_stocks_screen(user_name=user_name, list_type=lt)

    if mode == "stock_list":
        if list_type not in ("watchlist", "holdings"):
            raise HTTPException(status_code=400, detail="list_type must be watchlist or holdings")
        return _stock_list_screen(user_name=user_name, list_type=list_type, stocks=stocks)

    if mode == "no_stocks":
        if list_type not in ("watchlist", "holdings"):
            raise HTTPException(status_code=400, detail="list_type must be watchlist or holdings")
        return _no_stocks_screen(user_name=user_name, list_type=list_type)

    # ---- 정석 연결: summary ----
    if mode == "summary":
        if not ticker:
            return _stock_input_prompt_screen()

        symbol = _normalize_symbol(ticker)
        if not symbol:
            return _stock_not_found_screen()

        company_name = _resolve_company_name(bot, symbol, fallback="종목")
        summary_dict = bot.get_report_summary(symbol, company_name)

        # get_report_summary가 에러 dict를 주면 에러 포맷으로 변환
        return bot.format_summary_for_kakao(summary_dict)

    # ---- 정석 연결: topic_menu ----
    if mode == "topic_menu":
        return bot.format_topic_menu_for_kakao()

    # ---- 정석 연결: section ----
    if mode == "section":
        if not ticker:
            raise HTTPException(status_code=400, detail="ticker is required for section")

        symbol = _normalize_symbol(ticker)
        if not symbol:
            return _stock_not_found_screen()

        section_key = _normalize_section_key(bot, section)
        if not section_key:
            raise HTTPException(
                status_code=400,
                detail=f"invalid section. use one of: {list(bot.SECTIONS.keys())} or labels: {list(bot.SECTIONS.values())}"
            )

        company_name = _resolve_company_name(bot, symbol, fallback="종목")

        # section detail dict 생성 -> format dict
        detail_dict = bot.get_section_detail(symbol, company_name, section_key, raw_data=None)
        return bot.format_section_for_kakao(detail_dict)

    # ---- 정석 연결: all_sections ----
    if mode == "all_sections":
        if not ticker:
            raise HTTPException(status_code=400, detail="ticker is required for all_sections")

        symbol = _normalize_symbol(ticker)
        if not symbol:
            return _stock_not_found_screen()

        company_name = _resolve_company_name(bot, symbol, fallback="종목")

        all_sections_dict = bot.get_all_sections(symbol, company_name)
        return bot.format_all_sections_for_kakao(all_sections_dict)

    raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")