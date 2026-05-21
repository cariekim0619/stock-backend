# app/utils/gemini_compat.py
"""
`google.generativeai` -> `google.genai` 전환용 호환 래퍼.

목표:
- 기존 코드의 `GenerativeModel(...).generate_content(...)` 호출 방식을 최대한 유지
- 기본은 `google.genai`(신규 SDK) 사용
- 새 SDK가 아직 설치되지 않은 환경에서는 레거시 SDK로 임시 fallback 가능

권장 패키지:
    pip install -U google-genai
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional


class GeminiCompatClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self._mode = None
        self._client = None
        self._legacy = None

        try:
            from google import genai as google_genai  # type: ignore
            self._client = google_genai.Client(api_key=api_key) if api_key else google_genai.Client()
            self._mode = "google.genai"
            return
        except Exception as e_new:
            new_err = e_new

        try:
            import google.generativeai as legacy_genai  # type: ignore
            if api_key:
                legacy_genai.configure(api_key=api_key)
            self._legacy = legacy_genai
            self._mode = "google.generativeai"
            return
        except Exception as e_old:
            raise ImportError(
                "Gemini SDK를 초기화할 수 없습니다. `google-genai` 설치를 권장합니다. "
                f"new_sdk={new_err!r}, legacy_sdk={e_old!r}"
            ) from e_old

    def GenerativeModel(self, model_name: str, system_instruction: Optional[str] = None):
        if self._mode == "google.genai":
            return _GenAIModelCompat(self._client, model_name, system_instruction=system_instruction)
        return self._legacy.GenerativeModel(model_name, system_instruction=system_instruction)


class _GenAIModelCompat:
    def __init__(self, client: Any, model_name: str, system_instruction: Optional[str] = None):
        self._client = client
        self._model_name = model_name
        self._system_instruction = system_instruction

    def generate_content(self, contents: Any, generation_config: Optional[Dict[str, Any]] = None):
        config: Dict[str, Any] = {}
        if self._system_instruction:
            config["system_instruction"] = self._system_instruction
        if isinstance(generation_config, dict):
            config.update(generation_config)

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config or None,
        )
        return SimpleNamespace(text=_extract_response_text(response), raw_response=response)


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    chunks = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        for part in parts or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text:
                chunks.append(part_text)

    return "\n".join(chunks).strip()
