"""사용자 질문을 분석하여 검색에 적합한 질의로 변환한다."""

from __future__ import annotations

import json
import re
from typing import Any

from .llm import generate_answer


def build_query_analysis_prompt(query: str) -> str:
    """사용자 질문을 검색용 질의로 변환하기 위한 프롬프트를 생성한다."""
    return f"""
당신은 금융 RAG 시스템의 검색 질의 분석기입니다.

사용자의 질문을 분석하여 금융용어 사전 검색과 최신 뉴스 검색에
적합한 검색 질의를 생성하세요.

다음 기준을 반드시 지키세요.

1. intent
- 사용자가 궁금해하는 핵심 목적을 한 문장으로 작성합니다.

2. financial_term_query
- 사용자 질문 전체 의미를 금융 개념 중심으로 다시 작성합니다.
- 기업명, 종목명, 기사 제목은 제외합니다.
- "경제", "금융", "경기"처럼 너무 넓은 단어만 사용하지 않습니다.
- 질문의 원인을 설명할 수 있는 구체적인 금융 개념을 포함합니다.

3. financial_term_queries
- 한국은행 경제금융용어 사전에서 개별적으로 검색할 금융 개념을
  3개 이상 6개 이하의 배열로 작성합니다.
- 각 항목은 하나의 금융 개념 또는 짧은 금융 표현이어야 합니다.
- 사용자 질문과 직접적인 관련성이 높은 순서로 작성합니다.
- 서로 비슷한 표현을 반복하지 않습니다.
- 산업명이나 기업명보다 원인을 설명하는 금융 개념을 우선합니다.

4. news_query
- 최신 뉴스 검색에 사용할 질의를 작성합니다.
- 기업명, 산업명, 시장 상황, 사건, 시점 등을 구체적으로 포함합니다.

5. keywords
- 질문의 핵심 키워드를 3개 이상 6개 이하로 작성합니다.

예시 1:
사용자 질문: 최근 반도체 주가가 떨어진 이유가 뭐야?

financial_term_query:
주가 하락의 원인이 되는 시장금리 투자심리 위험회피 경기순환

financial_term_queries:
["주가 변동", "시장금리", "투자심리", "위험회피", "경기순환"]

news_query:
최근 삼성전자 SK하이닉스 반도체 주가 하락 원인 기술주 약세

예시 2:
사용자 질문: 기준금리가 오르면 주식은 왜 떨어질 수 있어?

financial_term_queries:
["기준금리", "시장금리", "주식가치", "채권가격", "할인율"]

반드시 아래 JSON 형식으로만 답변하세요.
마크다운 코드 블록과 추가 설명은 작성하지 마세요.

{{
  "intent": "질문의 핵심 의도",
  "financial_term_query": "금융용어 검색용 통합 질의",
  "financial_term_queries": [
    "금융 개념 1",
    "금융 개념 2",
    "금융 개념 3"
  ],
  "news_query": "뉴스 검색용 질의",
  "keywords": ["키워드1", "키워드2", "키워드3"]
}}

사용자 질문:
{query}
""".strip()


def extract_json_object(text: str) -> dict[str, Any]:
    """LLM 응답 문자열에서 JSON 객체를 추출한다."""
    cleaned_text = text.strip()

    cleaned_text = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned_text,
        flags=re.IGNORECASE,
    )
    cleaned_text = re.sub(
        r"\s*```$",
        "",
        cleaned_text,
    )

    try:
        parsed = json.loads(cleaned_text)

        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    json_match = re.search(
        r"\{.*\}",
        cleaned_text,
        flags=re.DOTALL,
    )

    if not json_match:
        raise ValueError("Gemini 응답에서 JSON 객체를 찾지 못했습니다.")

    parsed = json.loads(json_match.group())

    if not isinstance(parsed, dict):
        raise ValueError("Gemini 질문 분석 결과가 JSON 객체가 아닙니다.")

    return parsed


def normalize_keywords(value: Any) -> list[str]:
    """키워드 값을 문자열 리스트로 정리한다."""
    if not isinstance(value, list):
        return []

    keywords: list[str] = []

    for item in value:
        if not isinstance(item, str):
            continue

        clean_item = item.strip()

        if clean_item:
            keywords.append(clean_item)

    return keywords


def normalize_query_list(value: Any) -> list[str]:
    """Gemini가 생성한 검색어 배열을 안전한 문자열 리스트로 정리한다."""
    if not isinstance(value, list):
        return []

    queries: list[str] = []

    for item in value:
        if not isinstance(item, str):
            continue

        cleaned_item = item.strip()

        if cleaned_item and cleaned_item not in queries:
            queries.append(cleaned_item)

    return queries[:6]


def create_fallback_analysis(query: str) -> dict[str, Any]:
    """질문 분석 실패 시 원본 질문을 검색어로 사용한다."""
    return {
        "intent": query,
        "financial_term_query": query,
        "financial_term_queries": [query],
        "news_query": query,
        "keywords": [],
        "used_fallback": True,
    }


def analyze_user_query(query: str) -> dict[str, Any]:
    """Gemini를 이용해 사용자 질문을 검색용 질의로 분석한다."""
    clean_query = query.strip()

    if not clean_query:
        raise ValueError("분석할 사용자 질문이 비어 있습니다.")

    prompt = build_query_analysis_prompt(clean_query)

    try:
        response = generate_answer(prompt=prompt)
        parsed = extract_json_object(response)

        intent = str(parsed.get("intent", "")).strip()
        financial_term_query = str(
            parsed.get("financial_term_query", "")
        ).strip()
        news_query = str(
            parsed.get("news_query", "")
        ).strip()
        keywords = normalize_keywords(
            parsed.get("keywords", [])
        )
        financial_term_queries = normalize_query_list(
            parsed.get("financial_term_queries", [])
        )

        return {
            "intent": intent or clean_query,
            "financial_term_query": financial_term_query or clean_query,
            "financial_term_queries": (
                financial_term_queries
                if financial_term_queries
                else [financial_term_query or clean_query]
            ),
            "news_query": news_query or clean_query,
            "keywords": keywords,
            "used_fallback": False,
        }

    except (ValueError, TypeError, json.JSONDecodeError):
        return create_fallback_analysis(clean_query)
