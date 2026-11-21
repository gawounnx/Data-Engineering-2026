"""Gemini를 이용해 금융용어 검색 결과를 재정렬한다."""

from __future__ import annotations

import json
import re
from typing import Any

from .llm import generate_answer


def get_term_name(result: dict[str, Any]) -> str:
    """검색 결과에서 금융용어명을 추출한다."""
    metadata = result.get("metadata", {})

    if isinstance(metadata, dict):
        term = metadata.get("term")

        if isinstance(term, str) and term.strip():
            return term.strip()

    return "알 수 없는 금융용어"


def get_document(result: dict[str, Any]) -> str:
    """검색 결과에서 금융용어 설명을 추출한다."""
    document = result.get("document", "")

    if isinstance(document, str):
        return document.strip()

    return ""


def build_rerank_prompt(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int,
) -> str:
    """금융용어 후보 재정렬용 프롬프트를 생성한다."""
    candidate_blocks: list[str] = []

    for index, candidate in enumerate(candidates):
        term = get_term_name(candidate)
        document = get_document(candidate)

        candidate_blocks.append(
            "\n".join(
                [
                    f"후보 번호: {index}",
                    f"금융용어: {term}",
                    f"설명: {document[:700]}",
                ]
            )
        )

    candidates_text = "\n\n".join(candidate_blocks)

    return f"""
당신은 금융 RAG 검색 결과 재정렬기입니다.

사용자 질문과 금융용어 후보를 비교하여 질문을 설명하는 데
직접 도움이 되는 금융용어만 선택하세요.

선택 기준:
1. 질문의 핵심 원인이나 현상을 직접 설명할 수 있어야 합니다.
2. 단어 일부가 비슷하다는 이유만으로 선택하지 마세요.
3. 너무 포괄적이거나 간접적인 용어는 우선순위를 낮추세요.
4. 관련성이 낮은 후보는 선택하지 않아도 됩니다.
5. 최대 {top_k}개까지만 선택하세요.
6. 후보 번호는 아래 제공된 번호만 사용하세요.

반드시 아래 JSON 형식으로만 응답하세요.
마크다운 코드 블록과 추가 설명은 작성하지 마세요.

{{
  "selected_indices": [0, 2, 5],
  "reason": "선택 이유를 한 문장으로 작성"
}}

사용자 질문:
{query}

금융용어 후보:
{candidates_text}
""".strip()


def extract_json_object(text: str) -> dict[str, Any]:
    """Gemini 응답에서 JSON 객체를 추출한다."""
    cleaned_text = text.strip()

    cleaned_text = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    cleaned_text = re.sub(r"\s*```$", "", cleaned_text)

    try:
        parsed = json.loads(cleaned_text)

        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    json_match = re.search(r"\{.*\}", cleaned_text, flags=re.DOTALL)

    if not json_match:
        raise ValueError("재정렬 응답에서 JSON 객체를 찾지 못했습니다.")

    parsed = json.loads(json_match.group())

    if not isinstance(parsed, dict):
        raise ValueError("재정렬 결과가 JSON 객체가 아닙니다.")

    return parsed


def normalize_selected_indices(
    value: Any,
    candidate_count: int,
    top_k: int,
) -> list[int]:
    """선택된 후보 번호를 유효한 정수 리스트로 정리한다."""
    if not isinstance(value, list):
        return []

    selected_indices: list[int] = []

    for item in value:
        if not isinstance(item, int):
            continue

        if item < 0 or item >= candidate_count:
            continue

        if item in selected_indices:
            continue

        selected_indices.append(item)

        if len(selected_indices) >= top_k:
            break

    return selected_indices


def rerank_financial_terms(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int = 3,
) -> dict[str, Any]:
    """Gemini로 금융용어 후보를 재정렬한다."""
    if not candidates:
        return {
            "results": [],
            "reason": "재정렬할 금융용어 후보가 없습니다.",
            "used_fallback": False,
        }

    prompt = build_rerank_prompt(
        query=query,
        candidates=candidates,
        top_k=top_k,
    )

    try:
        response = generate_answer(prompt=prompt)
        parsed = extract_json_object(response)

        selected_indices = normalize_selected_indices(
            value=parsed.get("selected_indices", []),
            candidate_count=len(candidates),
            top_k=top_k,
        )

        if not selected_indices:
            return {
                "results": candidates[:top_k],
                "reason": "Gemini가 후보를 선택하지 않아 기존 순서를 사용했습니다.",
                "used_fallback": True,
            }

        return {
            "results": [
                candidates[index]
                for index in selected_indices
            ],
            "reason": str(parsed.get("reason", "")).strip(),
            "used_fallback": False,
        }

    except (ValueError, TypeError, json.JSONDecodeError):
        return {
            "results": candidates[:top_k],
            "reason": "재정렬에 실패하여 기존 검색 순서를 사용했습니다.",
            "used_fallback": True,
        }