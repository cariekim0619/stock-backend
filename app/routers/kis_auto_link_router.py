# -*- coding: utf-8 -*-
"""
KIS 자동 계좌연결 local agent 결과 수신 라우터.

Lambda가 S3에 생성한 kis_auto_jobs/<job_id>.json을 기준으로 job token을 검증하고,
Mac local agent/Selenium이 전달한 성공 또는 실패 결과를 같은 job 문서에 저장한다.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field


AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-2")
BUCKET = (os.getenv("KIS_AUTO_JOB_BUCKET") or os.getenv("BUCKET") or "stockpia-kakaotalk-chatbot-apse2-bucket").strip()
PREFIX = (os.getenv("KIS_AUTO_JOB_PREFIX") or "kis_auto_jobs").strip().strip("/") or "kis_auto_jobs"

s3 = boto3.client("s3", region_name=AWS_REGION)

router = APIRouter(prefix="/api/kis/auto-link", tags=["KIS Auto Link"])


class KisAutoLinkResultRequest(BaseModel):
    job_id: str = Field(..., description="Lambda가 생성한 KIS auto job ID")
    user_uuid: Optional[str] = Field(None, description="Kakao user uuid")
    status: str = Field("done", description="done 또는 failed")
    target_env: Optional[str] = None
    env: Optional[str] = None
    account_type: Optional[str] = None
    account_no: Optional[str] = None
    appkey: Optional[str] = None
    appsecret: Optional[str] = None
    error_type: Optional[str] = None
    message: Optional[str] = None
    source: Optional[str] = None
    created_at: Optional[str] = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_job_id(job_id: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\-]", "", (job_id or "").strip())
    if not safe:
        raise HTTPException(status_code=400, detail="job_id is required")
    return safe


def _job_key(job_id: str) -> str:
    return f"{PREFIX}/{_safe_job_id(job_id)}.json"


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def _bearer_token(authorization: Optional[str]) -> str:
    raw = (authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw.split(" ", 1)[1].strip()
    return raw


def _load_job(job_id: str) -> Dict[str, Any]:
    key = _job_key(job_id)
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        payload = json.loads(obj["Body"].read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("job json must be object")
        return payload
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            raise HTTPException(status_code=404, detail="KIS auto link job not found")
        raise HTTPException(status_code=500, detail=f"S3 read failed: {code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"job load failed: {type(e).__name__}")


def _save_job(job_id: str, payload: Dict[str, Any]) -> None:
    key = _job_key(job_id)
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def _verify_token(job: Dict[str, Any], authorization: Optional[str]) -> None:
    expected_hash = str(job.get("token_hash") or "").strip()
    if not expected_hash:
        raise HTTPException(status_code=403, detail="job token is not configured")
    token = _bearer_token(authorization)
    if not token or _hash_token(token) != expected_hash:
        raise HTTPException(status_code=403, detail="invalid job token")


def _validate_user(job: Dict[str, Any], user_uuid: Optional[str]) -> None:
    expected = str(job.get("user_uuid") or "").strip()
    got = str(user_uuid or "").strip()
    if expected and got and expected != got:
        raise HTTPException(status_code=403, detail="user_uuid does not match job")


@router.post("/result")
def receive_kis_auto_link_result(
    request: KisAutoLinkResultRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    job = _load_job(request.job_id)
    _verify_token(job, authorization)
    _validate_user(job, request.user_uuid)

    status = (request.status or "done").strip().lower()
    if status not in {"done", "success", "failed", "error"}:
        raise HTTPException(status_code=400, detail="status must be done/success/failed/error")

    now = _utc_now_iso()
    job["updated_at"] = now

    if status in {"failed", "error"}:
        job["status"] = "failed"
        job["result"] = {
            "status": "failed",
            "target_env": request.target_env,
            "error_type": request.error_type or "KIS_AUTO_LINK_FAILED",
            "message": request.message or "KIS 자동 연결 중 오류가 발생했습니다.",
            "source": request.source or "local_agent",
            "created_at": request.created_at or now,
        }
        _save_job(request.job_id, job)
        return {"ok": True, "status": "failed", "job_id": request.job_id}

    appkey = (request.appkey or "").strip()
    appsecret = (request.appsecret or "").strip()
    if not appkey or not appsecret:
        raise HTTPException(status_code=400, detail="appkey/appsecret are required for success result")

    job["status"] = "done"
    job["result"] = {
        "status": "done",
        "env": (request.env or request.target_env or job.get("target_env") or "real"),
        "target_env": request.target_env or job.get("target_env"),
        "account_type": request.account_type,
        "account_no": request.account_no,
        "appkey": appkey,
        "appsecret": appsecret,
        "source": request.source or "local_selenium",
        "created_at": request.created_at or now,
    }
    _save_job(request.job_id, job)
    return {"ok": True, "status": "done", "job_id": request.job_id}
