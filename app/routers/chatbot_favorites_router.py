from typing import Dict, List, Optional, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

# 프로젝트 구조에 맞게 import 경로를 수정하세요.
# 예:
# from app.service.chatbot_favorites import ChatbotFavorites
from app.services.chatbot_favorites import ChatbotFavorites


router = APIRouter(
    prefix="/api/stocks/chatbot/favorites",
    tags=["Chatbot Favorites"],
)


# =========================================================
# Request Models
# =========================================================

class FavoriteBaseRequest(BaseModel):
    user_id: str = Field(..., description="사용자 고유 ID")
    user_name: str = Field(..., description="사용자 이름")


class FavoriteSearchRequest(FavoriteBaseRequest):
    query: str = Field(..., description="검색할 종목명")


class FavoriteAddRequest(FavoriteBaseRequest):
    symbol: str = Field(..., description="종목 코드")
    company_name: str = Field(..., description="종목명")


class FavoriteDeleteRequest(FavoriteBaseRequest):
    company_name: str = Field(..., description="삭제할 종목명")


class FavoriteRecommendRequest(FavoriteBaseRequest):
    category: str = Field(..., description="volume 또는 return")
    segment: Optional[str] = Field("risk-neutral", description="skip 또는 risk-* 세그먼트")
    profile: Optional[Dict[str, Any]] = Field(None, description="설문 기반 개인화 프로필")


class FavoriteSummaryRequest(FavoriteBaseRequest):
    company_name: str = Field(..., description="요약 정보를 볼 관심 종목명")


# =========================================================
# Helper Functions
# =========================================================

def build_simple_text_response(
    text: str,
    quick_replies: Optional[List[Dict]] = None,
) -> Dict:
    """
    카카오 simpleText 응답 공통 생성 함수
    """
    response = {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text
                    }
                }
            ]
        }
    }

    if quick_replies:
        response["template"]["quickReplies"] = quick_replies

    return response


def build_quick_reply(
    label: str,
    message_text: str,
    block_id: Optional[str] = None,
    action: str = "block",
) -> Dict:
    """
    카카오 quickReply 1개 생성
    기본 action은 block으로 처리
    """
    item = {
        "action": action,
        "label": label,
        "messageText": message_text,
    }

    # message action일 때는 blockId를 넣지 않음
    if action == "block" and block_id:
        item["blockId"] = block_id

    return item


def build_search_not_found_response() -> Dict:
    """
    종목 검색 실패 응답
    """
    text = (
        "⚠️ 입력한 종목명을 찾을 수 없어요.\n\n"
        "다시 입력하거나, 종목명의 일부를 작성하면 해당 단어가 들어간 종목을 찾아드릴게요.\n"
        "아니면 추천 종목 확인 후 관심 종목을 등록하는 방법도 있어요 !"
    )

    quick_replies = [
        build_quick_reply(
            label="추천 종목 확인",
            message_text="추천 종목",
            block_id="favorite_recommend_block",
        ),
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_search_candidates_response(candidates: List[Dict]) -> Dict:
    """
    종목 일부 일치 후보 여러 개일 때 응답
    """
    number_emojis = ["➊", "➋", "➌", "➍", "➎"]

    lines = []
    quick_replies = []

    for idx, candidate in enumerate(candidates[:5]):
        company_name = candidate["company_name"]
        lines.append(f"{number_emojis[idx]} {company_name}")

        # 후보 종목은 사용자가 눌렀을 때 그대로 종목명 메시지를 보내도록 구성
        quick_replies.append(
            build_quick_reply(
                label=company_name,
                message_text=company_name,
                action="message",
            )
        )

    text = (
        "입력한 내용과 일부 일치하는 종목명을 찾았어요 !\n\n"
        "📁 종목 리스트\n"
        f"{chr(10).join(lines)}\n\n"
        "찾으시는 종목이 있으면 하단에 종목명이 있는 버튼을 눌러주세요 !\n\n"
        "⚠️ 찾으시는 종목이 없는 경우 다시 입력하거나, 추천 종목을 확인하는 방법도 있어요.\n"
        "(종목명의 일부를 자세하게 쓸수록 종목을 찾기 쉬워요 !)"
    )

    quick_replies.extend([
        build_quick_reply(
            label="추천 종목 확인",
            message_text="추천 종목",
            block_id="favorite_recommend_block",
        ),
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ])

    return build_simple_text_response(text, quick_replies)


def build_duplicate_response(company_name: str) -> Dict:
    """
    이미 관심 종목에 등록된 경우 응답
    """
    text = (
        f"⚠️ {company_name}은 이미 관심 종목에 등록되어 있어요.\n\n"
        "다른 종목을 추가하거나 요약 정보를 확인해보세요 !"
    )

    quick_replies = [
        build_quick_reply(
            label="관심 종목 요약 정보",
            message_text="관심 종목 요약",
            block_id="favorite_summary_block",
        ),
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_favorites_full_response(user_name: str) -> Dict:
    """
    관심 종목이 이미 10개인 경우 응답
    """
    text = (
        f"⚠️ 현재 {user_name}님의 관심 종목이 10개예요.\n\n"
        "관심 종목은 10개까지만 등록할 수 있기 때문에\n"
        "관심 종목을 삭제 후 다시 이용해주세요."
    )

    quick_replies = [
        build_quick_reply(
            label="관심 종목 삭제",
            message_text="관심 종목 삭제",
            block_id="favorite_delete_block",
        ),
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_delete_not_found_response() -> Dict:
    """
    삭제할 종목을 찾지 못한 경우
    """
    text = (
        "⚠️ 해당 종목을 찾을 수 없어요.\n\n"
        "종목명을 다시 입력해주세요."
    )

    quick_replies = [
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_summary_not_found_response() -> Dict:
    """
    관심 종목 요약 조회 시 대상 종목이 없는 경우
    """
    text = (
        "⚠️ 해당 종목은 관심 종목에 없거나 찾을 수 없어요.\n\n"
        "종목명을 다시 입력해주세요."
    )

    quick_replies = [
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_recommend_category_invalid_response() -> Dict:
    """
    추천 카테고리 값이 잘못 들어온 경우
    """
    text = (
        "⚠️ 추천 카테고리가 올바르지 않아요.\n\n"
        "거래량 TOP5 또는 상승률 TOP5로 다시 요청해주세요."
    )

    quick_replies = [
        build_quick_reply(
            label="거래량 TOP5",
            message_text="추천 volume",
            block_id="favorite_recommend_block",
        ),
        build_quick_reply(
            label="상승률 TOP5",
            message_text="추천 return",
            block_id="favorite_recommend_block",
        ),
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_recommend_empty_response() -> Dict:
    """
    추천 종목 조회 실패 시 응답
    """
    text = (
        "⚠️ 추천 종목 데이터를 불러오지 못했어요.\n"
        "잠시 후 다시 시도해주세요."
    )

    quick_replies = [
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_no_account_response() -> Dict:
    """
    계좌 미연동 상태
    """
    text = (
        "⚠️ 보유 종목을 불러오려면 먼저 계좌를 연동해야 해요.\n\n"
        "계좌 연동을 진행하시겠어요 ?"
    )

    quick_replies = [
        build_quick_reply(
            label="계좌 연동하기",
            message_text="계좌 연동",
            block_id="account_link_block",
        ),
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


def build_no_holdings_response(user_name: str) -> Dict:
    """
    계좌는 연동했지만 보유 종목이 없는 상태
    """
    text = (
        f"⚠️ {user_name}님의 보유 종목이 없어요.\n\n"
        "종목을 매수 후 다시 이용해주세요."
    )

    quick_replies = [
        build_quick_reply(
            label="메인으로",
            message_text="메인으로",
            block_id="main_block",
        ),
    ]

    return build_simple_text_response(text, quick_replies)


# =========================================================
# Endpoints
# =========================================================

@router.post("/entry")
def favorite_entry(request: FavoriteBaseRequest):
    """
    관심 종목 진입 화면
    - 관심 종목 개수에 따라 0개 / 1~9개 / 10개 자동 분기
    """
    chatbot = ChatbotFavorites()

    return chatbot.format_entry_for_kakao(
        user_id=request.user_id,
        user_name=request.user_name,
    )


@router.post("/search")
def favorite_search(request: FavoriteSearchRequest):
    """
    종목 검색 후 추가 플로우
    - 실패: 검색 실패 안내
    - 후보 여러 개: 후보 리스트 출력
    - 1개 매칭: 종목 요약 정보 출력
    """
    chatbot = ChatbotFavorites()

    search_result = chatbot.search_stock(request.query)

    # 검색 실패
    if not search_result.get("matched", False):
        return build_search_not_found_response()

    candidates = search_result.get("candidates", [])

    # 일부 일치 후보가 여러 개인 경우
    # 현재 업로드된 ChatbotFavorites.search_stock()는
    # 복수 후보가 있을 때도 matched=True + candidates 반환 구조라
    # 여기서 candidates 개수로 복수 후보를 판별합니다.
    if len(candidates) >= 2:
        return build_search_candidates_response(candidates)

    symbol = search_result["symbol"]
    company_name = search_result["company_name"]

    # 검색 결과 미리보기용 카드 데이터 수집
    # 현재 구현 파일에는 public 함수가 없어서 내부 메서드를 사용
    card1 = chatbot._collect_report_news_card(symbol, company_name)

    return chatbot.format_search_result_for_kakao(
        symbol=symbol,
        company_name=company_name,
        user_name=request.user_name,
        card1=card1,
    )


@router.post("/add")
def favorite_add(request: FavoriteAddRequest):
    """
    관심 종목 추가
    - full: 10개 초과
    - duplicate: 중복 등록
    - ok: 정상 등록
    """
    chatbot = ChatbotFavorites()

    result = chatbot.add_favorite(
        user_id=request.user_id,
        symbol=request.symbol,
        company_name=request.company_name,
    )

    reason = result.get("reason")

    if reason == "full":
        return build_favorites_full_response(request.user_name)

    if reason == "duplicate":
        return build_duplicate_response(request.company_name)

    return chatbot.format_add_complete_for_kakao(
        user_id=request.user_id,
        user_name=request.user_name,
        company_name=request.company_name,
    )


@router.post("/delete")
def favorite_delete(request: FavoriteDeleteRequest):
    """
    관심 종목 삭제
    """
    chatbot = ChatbotFavorites()

    result = chatbot.remove_favorite_by_name(
        user_id=request.user_id,
        company_name=request.company_name,
    )

    if not result.get("success", False):
        return build_delete_not_found_response()

    return chatbot.format_delete_complete_for_kakao(
        user_id=request.user_id,
        user_name=request.user_name,
        company_name=request.company_name,
    )


@router.post("/recommend")
def favorite_recommend(request: FavoriteRecommendRequest):
    """
    추천 종목 조회
    category:
    - volume : 거래량 TOP5
    - return : 상승률 TOP5
    """
    chatbot = ChatbotFavorites()

    category = request.category.strip().lower()

    if category not in {"volume", "return"}:
        return build_recommend_category_invalid_response()

    stocks = chatbot.get_top_stocks(category, segment=request.segment, profile=request.profile)

    if not stocks:
        return build_recommend_empty_response()

    holdings = chatbot.get_holdings_for_recommendation(limit=5)

    return chatbot.format_top_stocks_for_kakao(
        stocks=stocks,
        category=category,
        holdings=holdings,
    )


@router.post("/summary")
def favorite_summary(request: FavoriteSummaryRequest):
    """
    관심 종목 요약 정보
    - 관심 종목에 등록된 종목만 조회 가능
    """
    chatbot = ChatbotFavorites()

    favorites = chatbot.get_favorites(request.user_id)

    target = next(
        (item for item in favorites if item["company_name"] == request.company_name),
        None
    )

    if not target:
        return build_summary_not_found_response()

    symbol = target["symbol"]
    company_name = target["company_name"]

    summary_data = chatbot.get_summary_card_data(symbol, company_name)

    return chatbot.format_summary_carousel_for_kakao(
        symbol=symbol,
        company_name=company_name,
        card1=summary_data["card1"],
        card2=summary_data["card2"],
    )


@router.post("/load-holdings")
def favorite_load_holdings(request: FavoriteBaseRequest):
    """
    보유 종목을 관심 종목으로 불러오기
    - 먼저 관심 종목 10개 여부 확인
    - 그 다음 계좌 연동/보유 종목 상태 확인
    """
    chatbot = ChatbotFavorites()

    # 현재 관심 종목이 이미 10개인 경우 먼저 차단
    favorites = chatbot.get_favorites(request.user_id)
    if len(favorites) >= chatbot.MAX_FAVORITES:
        return build_favorites_full_response(request.user_name)

    result = chatbot.load_holdings_to_favorites(request.user_id)

    if not result.get("success", False):
        reason = result.get("reason")

        if reason == "no_account":
            return build_no_account_response()

        if reason == "no_holdings":
            return build_no_holdings_response(request.user_name)

        return build_simple_text_response(
            "⚠️ 보유 종목을 불러오는 중 문제가 발생했어요.\n잠시 후 다시 시도해주세요.",
            quick_replies=[
                build_quick_reply(
                    label="메인으로",
                    message_text="메인으로",
                    block_id="main_block",
                )
            ]
        )

    return chatbot.format_holdings_loaded_for_kakao(
        user_id=request.user_id,
        user_name=request.user_name,
        result=result,
    )