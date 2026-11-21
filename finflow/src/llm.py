"""Gemini API를 호출해 금융용어 답변을 생성한다."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from google import genai


load_dotenv()

DEFAULT_MODEL_NAME = "gemini-flash-latest"


def validate_prompt(prompt: str) -> str:
    """Gemini에 전달할 프롬프트를 검증한다."""
    clean_prompt = prompt.strip()

    if not clean_prompt:
        raise ValueError("Gemini에 전달할 프롬프트가 비어 있습니다.")

    return clean_prompt


def get_api_key() -> str:
    """환경변수에서 Gemini API 키를 읽는다."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY가 설정되지 않았습니다. "
            "finflow/.env 파일을 확인하세요."
        )

    return api_key


def get_model_name() -> str:
    """환경변수에서 Gemini 모델 이름을 읽는다."""
    model_name = os.getenv(
        "GEMINI_MODEL",
        DEFAULT_MODEL_NAME,
    ).strip()

    if not model_name:
        return DEFAULT_MODEL_NAME

    return model_name


@lru_cache(maxsize=1)
def load_gemini_client() -> genai.Client:
    """Gemini API 클라이언트를 생성한다."""
    api_key = get_api_key()

    return genai.Client(api_key=api_key)


def generate_answer(prompt: str) -> str:
    """프롬프트를 Gemini에 전달하고 답변을 반환한다."""
    clean_prompt = validate_prompt(prompt)
    client = load_gemini_client()
    model_name = get_model_name()

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=clean_prompt,
        )
    except Exception as error:
        raise RuntimeError(
            f"Gemini 답변 생성 중 오류가 발생했습니다: {error}"
        ) from error

    answer = (response.text or "").strip()

    if not answer:
        raise RuntimeError("Gemini가 빈 답변을 반환했습니다.")

    return answer
