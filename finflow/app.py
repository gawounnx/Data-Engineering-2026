from __future__ import annotations

from typing import Any
from uuid import uuid4

import streamlit as st

from src.rag import ask_finflow
from utils.event_logger import send_event


def render_header() -> None:
    """프로젝트 제목과 핵심 설명을 출력합니다."""
    st.title("FinFlow")
    st.subheader("금융용어를 쉽게 설명하는 AI 금융 지식 검색 서비스")

    st.markdown(
        """
        FinFlow는 한국은행 경제금융용어와 최신 금융뉴스를 연결하여,
        어려운 금융 개념과 실제 시장 상황을 함께 설명하는
        하이브리드 RAG 기반 금융 학습 서비스입니다.
        """
    )


def render_pipeline() -> None:
    """FinFlow의 전체 데이터 처리 흐름을 보여줍니다."""
    st.header("데이터 처리 흐름")

    st.markdown(
        """
        **금융용어 파이프라인**  
        한국은행 경제금융용어 PDF  
        → 텍스트 추출 및 품질 검증  
        → 문장 기반 청킹  
        → 텍스트 임베딩  
        → ChromaDB 저장  

        **금융뉴스 자동화 파이프라인**  
        네이버 금융뉴스 API  
        → 중복 제거 및 금융 관련성 검증  
        → GCS 원본 백업  
        → BigQuery 누적 적재  
        → 뉴스 청킹 및 임베딩  
        → ChromaDB 자동 갱신  
        → GCS 스냅샷 저장  

        **RAG 검색 흐름**  
        사용자 질문 분석  
        → 금융용어 벡터 검색  
        → 최신 금융뉴스 하이브리드 검색  
        → Gemini 재정렬  
        → 금융용어·뉴스 근거 기반 답변  

        **향후 확장**  
        - 한국은행 ECOS 경제지표 연동  
        - 주가 시계열 데이터 결합  
        - DART 공시 데이터 추가  
        - 질문 기간에 따른 동적 검색 범위 고도화  
        - 검색 품질 평가 및 운영 모니터링
        """
    )


def render_statistics() -> None:
    """프로젝트의 주요 데이터 규모를 카드로 출력합니다."""
    st.header("프로젝트 규모")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("금융용어", "770개")

    with col2:
        st.metric("벡터 데이터", "1,121개")

    with col3:
        st.metric("Vector DB", "ChromaDB")


def extract_answer(result: Any) -> str:
    """RAG 실행 결과에서 답변 문자열을 안전하게 추출합니다."""
    if isinstance(result, str):
        return result.replace("*", "")

    if isinstance(result, dict):
        for key in ("answer", "response", "generated_answer"):
            value = result.get(key)

            if isinstance(value, str):
                return value.replace("*", "")

    return str(result).replace("*", "")


def extract_references(result: Any) -> list[dict[str, Any]]:
    """RAG 결과에서 검색 근거 목록을 안전하게 추출합니다."""
    if not isinstance(result, dict):
        return []

    for key in ("references", "retrieved_terms", "search_results", "sources"):
        value = result.get(key)

        if isinstance(value, list):
            return [
                item
                for item in value
                if isinstance(item, dict)
            ]

    return []


def extract_news_sources(result: Any) -> list[dict[str, Any]]:
    """RAG 결과에서 금융 뉴스 목록을 안전하게 추출합니다."""
    if not isinstance(result, dict):
        return []

    news_sources = result.get("news_sources", [])

    if not isinstance(news_sources, list):
        return []

    return [
        news
        for news in news_sources
        if isinstance(news, dict)
    ]


def extract_query_analysis(result: Any) -> dict[str, Any]:
    """RAG 결과에서 Gemini 질문 분석 결과를 추출합니다."""
    if not isinstance(result, dict):
        return {}

    query_analysis = result.get("query_analysis", {})

    if not isinstance(query_analysis, dict):
        return {}

    return query_analysis


def extract_rerank_info(result: Any) -> dict[str, Any]:
    """RAG 결과에서 금융용어 재정렬 정보를 추출합니다."""
    if not isinstance(result, dict):
        return {}

    rerank_info = result.get("rerank_info", {})

    if not isinstance(rerank_info, dict):
        return {}

    return rerank_info


def render_reference(reference: dict[str, Any], index: int) -> None:
    """검색된 금융용어 한 건을 화면에 출력합니다."""
    metadata = reference.get("metadata", {})

    if not isinstance(metadata, dict):
        metadata = {}

    term = (
        reference.get("term")
        or reference.get("title")
        or metadata.get("term")
        or f"검색 결과 {index}"
    )

    score = (
        reference.get("similarity")
        or reference.get("score")
        or reference.get("distance")
    )

    st.markdown(f"**{index}. {term}**")

    if isinstance(score, (int, float)):
        st.caption(f"검색 점수: {score:.4f}")

    document = (
        reference.get("document")
        or reference.get("text")
        or reference.get("content")
    )

    if isinstance(document, str) and document.strip():
        with st.expander("검색된 원문 보기"):
            st.write(document)

    st.divider()


def render_news_article(
    article: dict[str, Any],
    index: int,
) -> None:
    """검색된 금융 뉴스 한 건을 화면에 출력합니다."""
    title = article.get("title", f"뉴스 {index}")
    summary = article.get("summary", "")
    published_at = article.get("published_at", "")
    category = article.get("category", "미분류")
    original_url = article.get("original_url", "")
    retrieval_score = article.get("retrieval_score")

    st.markdown(f"### {index}. {title}")

    caption_parts: list[str] = []

    if published_at:
        caption_parts.append(f"발행일: {published_at}")

    if category:
        caption_parts.append(f"분류: {category}")

    if isinstance(retrieval_score, (int, float)):
        caption_parts.append(f"검색 점수: {retrieval_score:.4f}")

    if caption_parts:
        st.caption(" · ".join(caption_parts))

    if isinstance(summary, str) and summary.strip():
        st.write(summary)

    if isinstance(original_url, str) and original_url.strip():
        st.link_button(
            "원문 기사 보기",
            original_url,
        )

    st.divider()


def render_query_analysis(
    query_analysis: dict[str, Any],
) -> None:
    """Gemini가 생성한 검색 질의를 화면에 표시합니다."""
    if not query_analysis:
        return

    with st.expander("Gemini 질문 분석 결과"):
        intent = query_analysis.get("intent", "")
        financial_term_query = query_analysis.get(
            "financial_term_query",
            "",
        )
        news_query = query_analysis.get(
            "news_query",
            "",
        )
        financial_term_queries = query_analysis.get(
            "financial_term_queries",
            [],
        )
        keywords = query_analysis.get("keywords", [])
        used_fallback = query_analysis.get(
            "used_fallback",
            False,
        )

        if intent:
            st.markdown(f"**질문 의도:** {intent}")

        if financial_term_query:
            st.markdown(
                f"**금융용어 검색어:** {financial_term_query}"
            )

        if (
            isinstance(financial_term_queries, list)
            and financial_term_queries
        ):
            st.markdown("**금융용어 세부 검색어:**")

            for term_query in financial_term_queries:
                st.markdown(f"- {term_query}")

        if news_query:
            st.markdown(
                f"**뉴스 검색어:** {news_query}"
            )

        if isinstance(keywords, list) and keywords:
            st.markdown(
                f"**핵심 키워드:** {', '.join(keywords)}"
            )

        if used_fallback:
            st.warning(
                "질문 분석에 실패하여 원본 질문을 검색어로 사용했습니다."
            )


def render_rerank_info(
    rerank_info: dict[str, Any],
) -> None:
    """금융용어 재정렬 정보를 화면에 표시합니다."""
    if not rerank_info:
        return

    with st.expander("금융용어 검색 고도화 결과"):
        candidate_count = rerank_info.get("candidate_count", 0)
        reason = rerank_info.get("reason", "")
        used_fallback = rerank_info.get("used_fallback", False)

        st.markdown(
            f"**ChromaDB 검색 후보 수:** {candidate_count}개"
        )

        if reason:
            st.markdown(f"**Gemini 재정렬 판단:** {reason}")

        if used_fallback:
            st.warning(
                "Gemini 재정렬에 실패하여 기존 검색 순서를 사용했습니다."
            )


def render_search() -> None:
    """사용자 질문을 받아 FinFlow RAG를 실행합니다."""
    st.header("금융 질문하기")

    with st.form("finflow_question_form"):
        question = st.text_input(
            "질문을 입력하세요",
            placeholder="예: 인플레이션이 무엇인가요?",
        )

        submitted = st.form_submit_button(
            "질문하기",
            use_container_width=True,
        )

    if not submitted:
        return

    cleaned_question = question.strip()

    if not cleaned_question:
        st.warning("질문을 입력해 주세요.")
        return

    send_event(
        event_type="record_search",
        session_id=st.session_state.session_id,
        page="finflow-search",
        properties={
            "question_length": len(cleaned_question),
        },
    )

    try:
        with st.spinner("질문을 분석하고 금융 정보를 검색하고 있습니다."):
            result = ask_finflow(cleaned_question)

        query_analysis = extract_query_analysis(result)
        rerank_info = extract_rerank_info(result)
        references = extract_references(result)
        news_sources = extract_news_sources(result)

        send_event(
            event_type="data_loaded",
            session_id=st.session_state.session_id,
            page="finflow-search",
            properties={
                "question_length": len(cleaned_question),
                "reference_count": len(references),
                "news_count": len(news_sources),
            },
        )

        render_query_analysis(query_analysis)
        render_rerank_info(rerank_info)

        st.header("AI 답변")
        st.markdown(extract_answer(result))

        if references:
            st.header("금융용어 검색 근거")

            for index, reference in enumerate(references, start=1):
                render_reference(reference, index)

        if news_sources:
            st.header("관련 금융 뉴스")

            for index, article in enumerate(news_sources, start=1):
                render_news_article(article, index)
        else:
            st.info("현재 질문과 관련된 금융 뉴스를 찾지 못했습니다.")

    except Exception as error:
        send_event(
            event_type="client_error",
            session_id=st.session_state.session_id,
            page="finflow-search",
            properties={
                "question_length": len(cleaned_question),
                "error_type": type(error).__name__,
                "error_code": "llm_request_failed",
            },
        )

        st.error(
            "답변을 생성하는 중 오류가 발생했습니다. "
            "잠시 후 다시 시도해 주세요."
        )

        with st.expander("오류 상세 정보"):
            st.code(str(error))


def render_technology_stack() -> None:
    """프로젝트에 사용한 주요 기술을 표시합니다."""
    st.header("사용 기술")

    st.write(
        """
        - **데이터 수집 및 전처리:** Python, JSON
        - **텍스트 임베딩:** SentenceTransformers
        - **Vector DB:** ChromaDB
        - **생성형 AI:** Gemini
        - **웹 애플리케이션:** Streamlit
        - **클라우드 데이터 파이프라인 실습:** Google Cloud Storage, BigQuery
        """
    )


def main() -> None:
    """FinFlow Streamlit 애플리케이션을 실행합니다."""
    st.set_page_config(
        page_title="FinFlow",
        page_icon="💰",
        layout="wide",
    )

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid4())

    if "page_view_logged" not in st.session_state:
        send_event(
            event_type="page_view",
            session_id=st.session_state.session_id,
            page="finflow-home",
            properties={
                "app": "finflow",
            },
        )
        st.session_state.page_view_logged = True

    render_header()
    st.divider()

    render_pipeline()
    st.divider()

    render_statistics()
    st.divider()

    render_search()

    st.divider()
    render_technology_stack()


if __name__ == "__main__":
    main()
