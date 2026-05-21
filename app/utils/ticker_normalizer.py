# app/utils/ticker_normalizer.py
"""
S3 기반 종목명/종목코드 정규화 유틸.

핵심 정책
- 종목 마스터는 S3의 단일 JSON (`stockpia-kakaotalk-chatbot-apse2-bucket-stockcache.json`)을 사용
- EC2는 S3 JSON이 있으면 그대로 사용하고, 없거나 비어 있을 때만 최초 1회 생성 시도
- 강제 갱신은 배치/운영 명령에서만 `force_refresh=True` 로 수행
- 런타임 메모리 캐시(TTL)로 EC2 프로세스 내 반복 조회 비용을 줄임
- 종목 마스터 원본은 OpenDART corpCode.xml 사용
"""
from __future__ import annotations

import io
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:  # pragma: no cover
    boto3 = None
    ClientError = Exception

KST = timezone(timedelta(hours=9))

AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")
S3_BUCKET = (
    os.getenv("STOCK_UNIVERSE_BUCKET")
    or os.getenv("BUCKET")
    or "stockpia-kakaotalk-chatbot-apse2-bucket"
).strip()
S3_KEY = (
    os.getenv("STOCK_UNIVERSE_KEY")
    or "stockpia-kakaotalk-chatbot-apse2-bucket-stockcache.json"
).strip()
DART_API_KEY = (os.getenv("DART_API_KEY") or "").strip()
DART_CORPCODE_URL = os.getenv(
    "DART_CORPCODE_URL",
    "https://opendart.fss.or.kr/api/corpCode.xml",
).strip()
RUNTIME_CACHE_TTL_SEC = int(os.getenv("STOCK_UNIVERSE_RUNTIME_TTL_SEC", "180"))
REFRESH_ENABLED = (os.getenv("STOCK_UNIVERSE_REFRESH_ENABLED", "true") or "true").strip().lower() not in {
    "0", "false", "no", "off"
}

# 최소 안전망 별칭
MANUAL_NAME_TO_CODE: Dict[str, str] = {
    "삼성전자": "005930",
    "삼성전기": "009150",
    "카카오": "035720",
    "현대차": "005380",
    "기아": "000270",
    "LG전자": "066570",
    "LG에너지솔루션": "373220",
    "SK하이닉스": "000660",
    "NAVER": "035420",
    "네이버": "035420",
    "삼성SDI": "006400",
    "셀트리온": "068270",
    "포스코": "005490",
    "POSCO홀딩스": "005490",
    "삼성바이오로직스": "207940",
}
MANUAL_CODE_TO_NAME: Dict[str, str] = {v: k for k, v in MANUAL_NAME_TO_CODE.items()}

_S3_CLIENT = None
_RUNTIME_CACHE_PAYLOAD: Optional[Dict[str, Any]] = None
_RUNTIME_CACHE_TS: float = 0.0
_RUNTIME_INDEX: Optional[Dict[str, Any]] = None
_RUNTIME_INDEX_SIG: str = ""


def _now_kst() -> datetime:
    return datetime.now(KST)


def _today_kst_str() -> str:
    return _now_kst().strftime("%Y%m%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_code(code: str) -> bool:
    s = (code or "").strip()
    return s.isdigit() and len(s) == 6


def _normalize_name_key(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = s.replace("주식회사", "")
    s = s.replace("(주)", "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", s)
    return s.lower().strip()


def _get_s3_client():
    global _S3_CLIENT
    if boto3 is None:
        return None
    if _S3_CLIENT is None:
        _S3_CLIENT = boto3.client("s3", region_name=AWS_REGION)
    return _S3_CLIENT


def _make_empty_payload() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "opendart_corpcode",
        "bucket": S3_BUCKET,
        "key": S3_KEY,
        "last_attempt_date": None,
        "last_success_date": None,
        "updated_at": None,
        "item_count": 0,
        "items": [],
        "last_error": None,
    }


def _payload_item_count(payload: Optional[Dict[str, Any]]) -> int:
    if not isinstance(payload, dict):
        return 0
    items = payload.get("items") or []
    return len(items) if isinstance(items, list) else 0


def _has_usable_s3_items(payload: Optional[Dict[str, Any]]) -> bool:
    return _payload_item_count(payload) > 0


def _s3_get_json() -> Optional[Dict[str, Any]]:
    if not S3_BUCKET or not S3_KEY:
        return None

    client = _get_s3_client()
    if client is None:
        return None

    try:
        obj = client.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
        payload = json.loads(obj["Body"].read().decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        payload.setdefault("items", [])
        payload["item_count"] = payload.get("item_count") or _payload_item_count(payload)
        return payload
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return None
        raise
    except Exception as e:
        print(f"[ticker_normalizer] S3 cache read failed: {e} bucket={S3_BUCKET} key={S3_KEY}")
        return None


def _s3_put_json(payload: Dict[str, Any]) -> None:
    if not S3_BUCKET or not S3_KEY:
        raise RuntimeError("S3 bucket/key not configured")

    client = _get_s3_client()
    if client is None:
        raise RuntimeError("boto3 S3 client unavailable")

    client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_KEY,
        Body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def _fetch_dart_corpcode_items() -> List[Dict[str, str]]:
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY not set")

    url = f"{DART_CORPCODE_URL}?crtfc_key={DART_API_KEY}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "stockpia-backend/1.0",
            "Accept": "application/zip, application/octet-stream, */*",
        },
        method="GET",
    )

    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()

    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        xml_name = None
        for name in zf.namelist():
            if name.lower().endswith(".xml"):
                xml_name = name
                break
        if not xml_name:
            raise RuntimeError("corpCode.xml not found in DART zip")
        xml_bytes = zf.read(xml_name)

    root = ET.fromstring(xml_bytes)
    items: List[Dict[str, str]] = []
    seen_codes = set()

    for node in root.findall(".//list"):
        corp_name = (node.findtext("corp_name") or "").strip()
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_code = (node.findtext("corp_code") or "").strip()
        if not corp_name or not _is_valid_code(stock_code):
            continue
        if stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        items.append({
            "name": corp_name,
            "code": stock_code,
            "corp_code": corp_code,
        })

    items.sort(key=lambda x: (x.get("name") or "", x.get("code") or ""))
    if not items:
        raise RuntimeError("empty stock universe from DART corpCode.xml")
    return items


def _mark_attempted(base: Optional[Dict[str, Any]], error_text: Optional[str] = None) -> Dict[str, Any]:
    payload = dict(base or _make_empty_payload())
    payload["schema_version"] = 1
    payload["source"] = "opendart_corpcode"
    payload["bucket"] = S3_BUCKET
    payload["key"] = S3_KEY
    payload["last_attempt_date"] = _today_kst_str()
    payload["last_error"] = error_text
    payload.setdefault("items", [])
    payload["item_count"] = _payload_item_count(payload)
    return payload


def _refresh_stock_universe(base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = _mark_attempted(base)
    try:
        items = _fetch_dart_corpcode_items()
        payload.update({
            "updated_at": _now_iso(),
            "last_success_date": _today_kst_str(),
            "last_error": None,
            "item_count": len(items),
            "items": items,
        })
        _s3_put_json(payload)
        print(
            f"[ticker_normalizer] stock universe refreshed to S3 "
            f"({len(items)} items) bucket={S3_BUCKET} key={S3_KEY}"
        )
        return payload
    except Exception as e:
        payload["last_error"] = repr(e)
        print(
            f"[ticker_normalizer] stock universe refresh failed: {e} "
            f"bucket={S3_BUCKET} key={S3_KEY}"
        )
        return payload


def _payload_signature(payload: Optional[Dict[str, Any]]) -> str:
    if not payload:
        return ""
    return (
        f"{payload.get('last_success_date')}|"
        f"{payload.get('last_attempt_date')}|"
        f"{payload.get('item_count')}|"
        f"{payload.get('updated_at')}"
    )


def _build_index(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    global _RUNTIME_INDEX, _RUNTIME_INDEX_SIG

    sig = _payload_signature(payload)
    if _RUNTIME_INDEX is not None and _RUNTIME_INDEX_SIG == sig:
        return _RUNTIME_INDEX

    code_to_name: Dict[str, str] = dict(MANUAL_CODE_TO_NAME)
    alias_to_code: Dict[str, str] = {
        _normalize_name_key(name): code for name, code in MANUAL_NAME_TO_CODE.items()
    }

    for item in (payload or {}).get("items", []) or []:
        name = (item.get("name") or "").strip()
        code = (item.get("code") or "").strip()
        if not name or not _is_valid_code(code):
            continue
        code_to_name.setdefault(code, name)
        alias_to_code[_normalize_name_key(name)] = code

    _RUNTIME_INDEX = {
        "code_to_name": code_to_name,
        "alias_to_code": alias_to_code,
    }
    _RUNTIME_INDEX_SIG = sig
    return _RUNTIME_INDEX


def ensure_stock_universe_cache(force_refresh: bool = False) -> Dict[str, Any]:
    """
    우선순위
    1) 런타임 캐시(TTL)
    2) S3 JSON 사용
    3) S3 JSON이 없거나 비어 있을 때만 최초 생성(refresh)
    4) 강제 갱신은 force_refresh=True 일 때만 수행
    """
    global _RUNTIME_CACHE_PAYLOAD, _RUNTIME_CACHE_TS

    now_ts = datetime.now(timezone.utc).timestamp()
    if (
        not force_refresh
        and _RUNTIME_CACHE_PAYLOAD is not None
        and (now_ts - _RUNTIME_CACHE_TS) < RUNTIME_CACHE_TTL_SEC
    ):
        return _RUNTIME_CACHE_PAYLOAD

    payload = _s3_get_json() or _make_empty_payload()

    if force_refresh:
        payload = _refresh_stock_universe(payload)
    elif _has_usable_s3_items(payload):
        print(
            f"[ticker_normalizer] stock universe loaded from S3 "
            f"({_payload_item_count(payload)} items) bucket={S3_BUCKET} key={S3_KEY}"
        )
    elif REFRESH_ENABLED:
        payload = _refresh_stock_universe(payload)
    else:
        print(
            "[ticker_normalizer] S3 stock universe cache unavailable and refresh disabled "
            f"bucket={S3_BUCKET} key={S3_KEY}"
        )

    _RUNTIME_CACHE_PAYLOAD = payload
    _RUNTIME_CACHE_TS = now_ts
    _build_index(payload)
    return payload


def warm_stock_universe_cache() -> Dict[str, Any]:
    return ensure_stock_universe_cache(force_refresh=False)


def get_lookup_status() -> Dict[str, Any]:
    payload = ensure_stock_universe_cache(force_refresh=False)
    return {
        "bucket": S3_BUCKET,
        "key": S3_KEY,
        "refresh_enabled": REFRESH_ENABLED,
        "last_attempt_date": payload.get("last_attempt_date"),
        "last_success_date": payload.get("last_success_date"),
        "updated_at": payload.get("updated_at"),
        "item_count": payload.get("item_count") or _payload_item_count(payload),
        "has_s3_items": _has_usable_s3_items(payload),
        "has_usable_data": bool((payload.get("items") or []) or MANUAL_NAME_TO_CODE),
        "last_error": payload.get("last_error"),
    }


def force_refresh_stock_universe_cache() -> Dict[str, Any]:
    return ensure_stock_universe_cache(force_refresh=True)


def normalize_ticker(ticker: str) -> str:
    raw = ("" if ticker is None else str(ticker)).strip()
    if not raw:
        return ""
    resolved = resolve_symbol_and_name(raw)
    return resolved[0] if resolved else raw


def get_company_name_by_symbol(symbol: str) -> Optional[str]:
    sym = (symbol or "").strip()
    if not _is_valid_code(sym):
        return None
    payload = ensure_stock_universe_cache(force_refresh=False)
    index = _build_index(payload)
    return index["code_to_name"].get(sym)


def resolve_symbol_and_name(ticker: str, allow_unresolved: bool = False) -> Optional[Tuple[str, str]]:
    raw = ("" if ticker is None else str(ticker)).strip()
    if not raw:
        return None

    payload = ensure_stock_universe_cache(force_refresh=False)
    index = _build_index(payload)
    code_to_name: Dict[str, str] = index["code_to_name"]
    alias_to_code: Dict[str, str] = index["alias_to_code"]

    if raw.isdigit():
        code = raw.zfill(6) if len(raw) < 6 else raw
        if _is_valid_code(code) and code in code_to_name:
            return code, code_to_name[code]
        return (code, raw) if (allow_unresolved and _is_valid_code(code)) else None

    code = alias_to_code.get(_normalize_name_key(raw))
    if code and code in code_to_name:
        return code, code_to_name[code]

    code = MANUAL_NAME_TO_CODE.get(raw)
    if code:
        return code, MANUAL_CODE_TO_NAME.get(code, raw)

    return (raw, raw) if allow_unresolved else None


if __name__ == "__main__":
    status = get_lookup_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    for probe in ("삼성전자", "삼성전기", "카카오", "005930"):
        print(probe, "->", resolve_symbol_and_name(probe))
