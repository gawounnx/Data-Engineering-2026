"""금융용어와 금융뉴스 검색 결과를 이용해 RAG 프롬프트를 생성한다."""

from __future__ import annotations

from typing import Any


SYSTEM_INSTRUCTION = """
당신은 금융 초보자를 돕는 FinFlow 금융 정보 AI입니다.

반드시 제공된 금융용어 자료와 뉴스 자료를 중심으로 답변하세요.

답변 규칙:
1. 한국어로 답변합니다.
2. 금융 초보자가 이해할 수 있도록 쉽고 자연스러운 표현을 사용합니다.
3. 질문이 금융 개념에 관한 것이면 핵심 정의를 먼저 설명합니다.
4. 정의만 나열하지 말고, 개념이 어떻게 작동하는지 함께 설명합니다.
5. 어려운 전문용어를 사용하면 바로 쉬운 말로 풀어서 설명합니다.
6. 질문 이해에 도움이 될 때만 실생활 예시나 경제 상황을 추가합니다.
7. 질문이 최근 시장 상황에 관한 것이면 관련 뉴스의 핵심 내용을 먼저 정리합니다.
8. 금융용어와 뉴스가 직접 관련될 때만 두 자료를 연결해 설명합니다.
9. 참고 자료에 없는 사실은 확실한 것처럼 추측하지 않습니다.
10. 사용자의 질문과 직접 관련된 참고 자료만 사용합니다.
11. 질문과 관련이 낮은 금융용어나 뉴스는 답변에 포함하지 않습니다.
12. 최신 뉴스가 없으면 현재 상황을 정확히 판단하기 어렵다고 알립니다.
13. 투자 판단을 단정적으로 권유하지 않습니다.
14. 같은 내용을 반복하지 않습니다.
15. 답변은 핵심이 분명하도록 작성하되, 이해에 필요한 설명은 충분히 제공합니다.
16. 별표(*, **, ***)를 포함한 마크다운 강조 문법을 사용하지 않습니다.
""".strip()


def validate_prompt_input(
    query: str,
    search_results: list[dict[str, Any]],
    news_results: list[dict[str, Any]],
) -> None:
    """프롬프트 생성에 필요한 입력값을 검증한다."""
    if not query.strip():
        raise ValueError("사용자 질문이 비어 있습니다.")

    if not isinstance(search_results, list):
        raise TypeError(
            "search_results는 리스트 형식이어야 합니다."
        )

    if not isinstance(news_results, list):
        raise TypeError(
            "news_results는 리스트 형식이어야 합니다."
        )


def build_term_context(
    search_results: list[dict[str, Any]],
) -> str:
    """금융용어 검색 결과를 참고 자료 문자열로 변환한다."""
    if not search_results:
        return "검색된 금융용어 자료가 없습니다."

    context_blocks: list[str] = []

    for result in search_results:
        rank = result.get("rank", "?")
        term = result.get("term", "알 수 없음")
        document = result.get("document", "")
        similarity = result.get("similarity")

        if similarity is None:
            similarity_text = "알 수 없음"
        else:
            similarity_text = f"{float(similarity):.4f}"

        context_block = (
            f"[금융용어 자료 {rank}]\n"
            f"금융용어: {term}\n"
            f"검색 유사도: {similarity_text}\n"
            f"내용:\n{document}"
        )

        context_blocks.append(context_block)

    return "\n\n".join(context_blocks)


def build_news_context(
    news_results: list[dict[str, Any]],
) -> str:
    """금융뉴스 검색 결과를 참고 자료 문자열로 변환한다."""
    if not news_results:
        return "검색된 관련 뉴스가 없습니다."

    context_blocks: list[str] = []

    for rank, article in enumerate(news_results, start=1):
        title = article.get("title", "제목 없음")
        summary = article.get("summary", "요약 없음")
        published_at = article.get("published_at", "날짜 없음")
        category = article.get("category", "미분류")
        original_url = article.get("original_url", "")

        context_block = (
            f"[뉴스 자료 {rank}]\n"
            f"제목: {title}\n"
            f"발행일: {published_at}\n"
            f"카테고리: {category}\n"
            f"요약: {summary}\n"
            f"원문 URL: {original_url}"
        )

        context_blocks.append(context_block)

    return "\n\n".join(context_blocks)


def build_rag_prompt(
    query: str,
    search_results: list[dict[str, Any]],
    news_results: list[dict[str, Any]],
) -> str:
    """질문, 금융용어, 뉴스 검색 결과를 결합해 최종 프롬프트를 만든다."""
    clean_query = query.strip()

    validate_prompt_input(
        query=clean_query,
        search_results=search_results,
        news_results=news_results,
    )

    term_context = build_term_context(
        search_results=search_results,
    )

    news_context = build_news_context(
        news_results=news_results,
    )

    prompt = f"""
{SYSTEM_INSTRUCTION}

아래 금융용어 자료와 뉴스 자료를 바탕으로 사용자의 질문에 답변하세요.

[사용자 질문]
{clean_query}

[금융용어 자료]
{term_context}

[금융뉴스 자료]
{news_context}

[중요 규칙]
- 질문과 직접 관련된 자료만 선택해서 사용하세요.
- 모든 금융용어와 뉴스를 억지로 답변에 포함하지 마세요.
- 뉴스 자료는 발행일을 고려해서 설명하세요.
- 참고 자료에 없는 내용을 추측하거나 만들어내지 마세요.
- 질문에 답할 근거가 부족하면 정보가 부족하다고 명확히 말하세요.
- 검색 유사도와 검색 점수는 답변에 포함하지 마세요.
- 투자 매수·매도 판단을 단정적으로 권유하지 마세요.

[답변 작성 방식]
- 금융 개념 질문이면 핵심 정의를 먼저 설명하세요.
- 최근 시장 질문이면 관련 뉴스의 핵심 내용을 먼저 2~3문장으로 정리하세요.
- 왜 중요한지 개인, 가계, 기업 또는 경제 관점에서 설명하세요.
- 가능한 경우 초보자가 이해하기 쉬운 실생활 예시를 들어 설명하세요.
- 필요한 경우 금융용어와 뉴스의 관계를 연결해서 설명하세요.
- 질문과 관계없는 항목은 억지로 작성하지 마세요.
- 실제로 답변에 사용한 금융용어와 뉴스만 마지막에 표시하세요.

[출력 형식]
핵심 답변

필요한 경우 추가 설명 또는 예시

참고한 금융용어: 용어명
참고한 뉴스: 기사 제목

별표를 이용한 굵게 또는 기울임 표시는 사용하지 마세요.
""".strip()

    return prompt
