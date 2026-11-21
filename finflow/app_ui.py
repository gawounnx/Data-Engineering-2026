from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import streamlit as st

from src.news_snapshot import (
    DATA_SETUP_GUIDE,
    NewsSnapshotStatus,
    sync_latest_news_snapshot,
)
from src.rag import ask_finflow
from utils.event_logger import send_event


KST = ZoneInfo("Asia/Seoul")
PIPELINE_FRESHNESS_LIMIT = timedelta(minutes=90)


def load_css(css_path: str = "styles/main.css") -> None:
    """외부 CSS 파일을 읽어 Streamlit 화면에 적용합니다."""
    path = Path(css_path)

    if not path.exists():
        st.warning(f"CSS 파일을 찾지 못했습니다: {css_path}")
        return

    try:
        css = path.read_text(encoding="utf-8")
    except OSError as error:
        st.warning(f"CSS 파일을 읽지 못했습니다: {error}")
        return

    st.markdown(
        f"<style>{css}</style>",
        unsafe_allow_html=True,
    )


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


def render_sidebar() -> str:
    """FinFlow의 왼쪽 내비게이션 메뉴를 출력합니다."""
    with st.sidebar:
        st.markdown("## FinFlow")
        st.caption("Financial Intelligence Workspace")

        selected_page = st.radio(
            "메뉴",
            [
                "AI 금융 검색",
                "시장 브리핑",
                "데이터 파이프라인",
                "프로젝트 정보",
            ],
            label_visibility="collapsed",
        )

        st.divider()
        st.caption("Hybrid RAG · Gemini · ChromaDB")

        st.button(
            "＋ 새 분석",
            width="stretch",
            on_click=reset_search,
        )

    return selected_page


def is_pipeline_fresh(status: NewsSnapshotStatus) -> bool:
    """최신 스냅샷이 매시간 갱신 기준을 충족하는지 확인합니다."""
    if (
        not status.data_ready
        or status.updated_at is None
        or status.error
    ):
        return False

    return (
        datetime.now(timezone.utc) - status.updated_at
        <= PIPELINE_FRESHNESS_LIMIT
    )


def format_pipeline_updated_at(status: NewsSnapshotStatus) -> str:
    """최근 데이터 갱신 시각을 한국 시간으로 표시합니다."""
    if status.updated_at is None:
        return "확인할 수 없음"

    return status.updated_at.astimezone(KST).strftime(
        "%Y.%m.%d %H:%M"
    )


def render_topbar(status: NewsSnapshotStatus) -> None:
    """현재 페이지 제목과 서비스 상태를 출력합니다."""
    title_col, status_col = st.columns([7, 3])

    with title_col:
        st.markdown(
            """
            <section class="finflow-hero">
                <div class="finflow-brand">FinFlow</div>
                <h1>금융 개념과 최신 뉴스를 연결하는 AI 검색 서비스</h1>
                <p>
                    한국은행 금융용어와 최신 금융 뉴스를 함께 검색해
                    어려운 금융 개념과 시장 상황을 쉽게 설명합니다.
                </p>
            </section>
            """,
            unsafe_allow_html=True,
        )

    with status_col:
        if not status.data_ready:
            st.markdown("**NO DATA**")
            st.error("검색 데이터 준비 필요")
        elif is_pipeline_fresh(status):
            st.markdown("**LIVE**")
            st.success("파이프라인 정상")
        else:
            st.markdown("**UPDATE DELAYED**")
            st.warning("뉴스 업데이트 지연")

        st.caption(
            "최근 데이터 갱신: "
            f"{format_pipeline_updated_at(status)}"
        )

        if status.error:
            st.caption(f"상태 확인 오류: {status.error}")


def render_home_statistics(status: NewsSnapshotStatus) -> None:
    """메인 화면에 프로젝트 데이터 규모를 출력합니다."""
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        with st.container(border=True):
            st.caption("금융용어")
            st.metric("Terms", "770개")

    with col2:
        with st.container(border=True):
            st.caption("Vector Data")
            st.metric("Chunks", "1,121개")

    with col3:
        with st.container(border=True):
            st.caption("금융뉴스")
            st.metric("Articles", "373건")

    with col4:
        with st.container(border=True):
            st.caption("Pipeline")
            st.metric(
                "Status",
                (
                    "No data"
                    if not status.data_ready
                    else "Healthy"
                    if is_pipeline_fresh(status)
                    else "Delayed"
                ),
            )


def render_pipeline(status: NewsSnapshotStatus) -> None:
    """FinFlow 데이터 파이프라인 상태를 시각적으로 보여줍니다."""
    st.markdown("## 데이터 파이프라인")
    st.caption(
        "금융용어와 금융뉴스가 수집·검증·임베딩되어 "
        "RAG 검색에 연결되는 전체 흐름입니다."
    )

    status_col, updated_col = st.columns([1, 3])

    with status_col:
        if not status.data_ready:
            st.error("검색 데이터 없음")
        elif is_pipeline_fresh(status):
            st.success("전체 파이프라인 정상")
        else:
            st.warning("뉴스 파이프라인 업데이트 지연")

    with updated_col:
        st.caption(
            "최근 스냅샷: "
            f"{format_pipeline_updated_at(status)}"
        )

    st.write("")

    metric_cols = st.columns(4)

    with metric_cols[0]:
        with st.container(border=True):
            st.caption("신규 금융뉴스")
            st.metric("Articles", "80건")

    with metric_cols[1]:
        with st.container(border=True):
            st.caption("BigQuery")
            st.metric("Rows", "373건")

    with metric_cols[2]:
        with st.container(border=True):
            st.caption("Vector Data")
            st.metric("Chunks", "1,121개")

    with metric_cols[3]:
        with st.container(border=True):
            st.caption("처리 시간")
            st.metric("Duration", "2m 14s")

    st.write("")
    st.markdown("### 전체 처리 흐름")

    pipeline_steps = [
        ("Naver News API", "금융뉴스 수집"),
        ("Raw JSON", "원본 데이터 저장"),
        ("GCS", "원본 백업"),
        ("BigQuery", "메타데이터 적재"),
        ("Quality Filter", "중복·관련성 검증"),
        ("Chunking", "문장 단위 분할"),
        ("Embedding", "텍스트 벡터화"),
        ("ChromaDB", "벡터 저장"),
        ("Gemini RAG", "근거 기반 답변"),
    ]

    for index, (name, description) in enumerate(pipeline_steps):
        with st.container(border=True):
            left, right = st.columns([1, 4])

            with left:
                st.markdown(f"**{index + 1:02d}**")

            with right:
                st.markdown(f"**{name}**")
                st.caption(description)

        if index < len(pipeline_steps) - 1:
            st.markdown(
                "<div style='text-align:center; color:#98A2B3; "
                "font-size:24px;'>↓</div>",
                unsafe_allow_html=True,
            )

    st.write("")

    term_tab, news_tab, logs_tab = st.tabs(
        [
            "금융용어 파이프라인",
            "금융뉴스 파이프라인",
            "최근 실행 로그",
        ]
    )

    with term_tab:
        st.markdown(
            """
            **한국은행 경제금융용어 PDF**  
            → 텍스트 추출 및 품질 검증  
            → 문장 기반 청킹  
            → SentenceTransformer 임베딩  
            → ChromaDB `financial_terms` 저장
            """
        )

        st.info("금융용어 770개 · 벡터 청크 1,121개")

    with news_tab:
        st.markdown(
            """
            **네이버 금융뉴스 API**  
            → 중복 제거 및 금융 관련성 검증  
            → GCS 원본 백업  
            → BigQuery 누적 적재  
            → 뉴스 청킹 및 임베딩  
            → ChromaDB `financial_news` 갱신
            """
        )

        st.info("현재 BigQuery 누적 373건")

    with logs_tab:
        st.write(
            {
                "상태": (
                    "정상" if is_pipeline_fresh(status) else "지연"
                ),
                "최근 스냅샷": format_pipeline_updated_at(status),
                "스냅샷 세대": status.generation or "확인 불가",
            }
        )

        if status.error:
            st.error(f"상태 확인 오류: {status.error}")

    with st.expander("향후 확장 계획"):
        st.markdown(
            """
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
        return result

    if isinstance(result, dict):
        for key in ("answer", "response", "generated_answer"):
            value = result.get(key)

            if isinstance(value, str):
                return value

    return str(result)


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


def extract_reference_terms(
    references: list[dict[str, Any]],
) -> list[str]:
    """RAG 검색 근거에서 금융용어 이름을 추출합니다."""
    terms: list[str] = []

    for reference in references:
        metadata = reference.get("metadata", {})

        if not isinstance(metadata, dict):
            metadata = {}

        candidates = [
            reference.get("term"),
            reference.get("title"),
            reference.get("keyword"),
            metadata.get("term"),
            metadata.get("title"),
            metadata.get("keyword"),
        ]

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                terms.append(candidate.strip())
                break

    return list(dict.fromkeys(terms))


def highlight_terms_in_answer(
    answer: str,
    terms: list[str],
) -> str:
    """
    답변에 실제로 등장한 금융용어만 강조합니다.

    검색 근거에 용어가 있어도 답변 본문에 없으면
    강제로 추가하거나 강조하지 않습니다.
    """
    # Gemini가 강조용 Markdown을 반환해도 HTML 화면에 별표가 노출되지
    # 않도록 렌더링 전에 강조 구분자를 제거합니다.
    clean_answer = answer.replace("*", "")
    safe_answer = html.escape(clean_answer)

    valid_terms = sorted(
        {
            term.strip()
            for term in terms
            if isinstance(term, str)
            and len(term.strip()) >= 2
            and term.strip().lower() in clean_answer.lower()
        },
        key=len,
        reverse=True,
    )

    for term in valid_terms:
        escaped_term = html.escape(term)

        safe_answer = re.sub(
            re.escape(escaped_term),
            (
                '<mark class="financial-term-highlight">'
                f"{escaped_term}"
                "</mark>"
            ),
            safe_answer,
            flags=re.IGNORECASE,
        )

    return safe_answer.replace("\n", "<br>")


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


def set_suggested_question(question: str) -> None:
    """추천 질문을 다음 실행에서 입력창에 반영합니다."""
    st.session_state["pending_suggestion"] = question


def reset_search() -> None:
    """현재 질문과 검색 결과를 초기화합니다."""
    st.session_state.search_question = ""
    st.session_state.search_result = None


def render_search_analysis(
    query_analysis: dict[str, Any],
    rerank_info: dict[str, Any],
    reference_count: int,
    news_count: int,
) -> None:
    """RAG 검색 과정의 핵심 정보를 요약 카드로 출력합니다."""
    intent = query_analysis.get("intent") or "금융 정보 탐색"
    candidate_count = rerank_info.get("candidate_count", 0)
    used_fallback = bool(
        query_analysis.get("used_fallback")
        or rerank_info.get("used_fallback")
    )

    with st.container(border=True):
        st.markdown("### 검색 분석")

        st.caption("질문 유형")
        st.markdown(f"**{intent}**")

        st.caption("검색 방식")
        st.markdown("**Hybrid Search**")

        st.caption("검색 후보")
        if candidate_count:
            st.markdown(f"**금융용어 {candidate_count}건**")
        else:
            st.markdown("**Vector + Keyword**")

        st.caption("최종 근거")
        st.markdown(
            f"**금융용어 {reference_count}건 · 뉴스 {news_count}건**"
        )

        st.caption("답변 생성")
        st.markdown("**Gemini**")

        if used_fallback:
            st.warning("일부 검색 과정에서 기본 검색어를 사용했습니다.")


def render_search_results(result: Any) -> None:
    """Stitch 시안을 반영한 RAG 검색 결과 화면을 출력합니다."""
    query_analysis = extract_query_analysis(result)
    rerank_info = extract_rerank_info(result)
    references = extract_references(result)
    news_sources = extract_news_sources(result)
    answer = extract_answer(result)
    reference_terms = extract_reference_terms(references)

    highlighted_answer = highlight_terms_in_answer(
        answer=answer,
        terms=reference_terms,
    )

    st.markdown("---")

    answer_col, analysis_col = st.columns([8, 4], gap="large")

    with answer_col:
        with st.container(border=True):
            st.caption(
                f"금융용어 {len(references)}건 · "
                f"금융뉴스 {len(news_sources)}건 활용"
            )
            st.markdown("### AI 분석 결과")
            st.markdown(
                f"""
                <div class="ai-answer-content">
                    {highlighted_answer}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with analysis_col:
        render_search_analysis(
            query_analysis=query_analysis,
            rerank_info=rerank_info,
            reference_count=len(references),
            news_count=len(news_sources),
        )

    st.write("")

    term_tab, news_tab, detail_tab = st.tabs(
        [
            f"금융용어 근거 {len(references)}",
            f"관련 뉴스 {len(news_sources)}",
            "검색 상세",
        ]
    )

    with term_tab:
        if references:
            for index, reference in enumerate(references, start=1):
                with st.container(border=True):
                    render_reference(reference, index)
        else:
            st.info("검색된 금융용어 근거가 없습니다.")

    with news_tab:
        render_news_results(news_sources)

    with detail_tab:
        render_query_analysis(query_analysis)
        render_rerank_info(rerank_info)

        with st.expander("검색 결과 원본 구조 보기"):
            st.json(
                {
                    "reference_count": len(references),
                    "news_count": len(news_sources),
                    "query_analysis": query_analysis,
                    "rerank_info": rerank_info,
                }
            )


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
    """관련 뉴스 한 건을 compact 카드로 출력합니다."""
    title = str(
        article.get("title")
        or article.get("news_title")
        or f"관련 뉴스 {index}"
    ).strip()

    summary = str(
        article.get("summary")
        or article.get("description")
        or article.get("content")
        or ""
    ).strip()

    published_at = str(
        article.get("published_at")
        or article.get("pub_date")
        or article.get("date")
        or ""
    ).strip()

    category = str(
        article.get("category")
        or article.get("section")
        or ""
    ).strip()

    url = str(
        article.get("original_url")
        or article.get("url")
        or article.get("link")
        or article.get("originallink")
        or ""
    ).strip()

    score = article.get("score")
    if score is None:
        score = article.get("distance")

    max_summary_length = 170
    if len(summary) > max_summary_length:
        summary = f"{summary[:max_summary_length].rstrip()}…"

    metadata_parts = [
        value
        for value in [published_at, category]
        if value
    ]
    metadata_text = " · ".join(metadata_parts)

    safe_title = html.escape(title)
    safe_summary = html.escape(summary)
    safe_metadata = html.escape(metadata_text)

    with st.container(border=True):
        st.markdown(
            f"""
            <article class="news-card">
                <h4 class="news-card-title">{safe_title}</h4>
                {f'<p class="news-card-meta">{safe_metadata}</p>' if safe_metadata else ''}
                {f'<p class="news-card-summary">{safe_summary}</p>' if safe_summary else ''}
            </article>
            """,
            unsafe_allow_html=True,
        )

        footer_left, footer_right = st.columns(
            [3, 1],
            vertical_alignment="center",
        )

        with footer_left:
            if isinstance(score, (int, float)):
                st.caption(f"검색 관련도 · {float(score):.3f}")
            else:
                st.caption("FinFlow 관련 뉴스")

        with footer_right:
            if url:
                st.link_button(
                    "원문 보기",
                    url,
                    width="stretch",
                )


def render_news_results(
    news_articles: list[dict[str, Any]],
) -> None:
    """관련 뉴스를 최대 6건, 2열 카드로 표시합니다."""
    if not news_articles:
        st.info("검색된 관련 뉴스가 없습니다.")
        return

    visible_articles = news_articles[:6]

    for row_start in range(0, len(visible_articles), 2):
        left_column, right_column = st.columns(
            2,
            gap="medium",
        )

        with left_column:
            render_news_article(
                article=visible_articles[row_start],
                index=row_start + 1,
            )

        right_index = row_start + 1

        if right_index < len(visible_articles):
            with right_column:
                render_news_article(
                    article=visible_articles[right_index],
                    index=right_index + 1,
                )


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


def render_data_setup_notice(status: NewsSnapshotStatus) -> None:
    """검색 데이터가 없을 때 원인과 준비 방법을 안내합니다."""
    missing = ", ".join(status.missing_collections)
    st.error(
        "검색에 필요한 ChromaDB 컬렉션이 준비되지 않아 "
        f"질문을 처리할 수 없습니다. (누락·빈 컬렉션: {missing})"
    )

    with st.expander("데이터 준비 방법", expanded=True):
        st.markdown(DATA_SETUP_GUIDE)
        st.code(
            "python scripts/vectorstores/download_chroma_snapshot.py",
            language="bash",
        )

        if status.error:
            st.caption(f"상태 확인 결과: {status.error}")


def render_search(status: NewsSnapshotStatus) -> None:
    """사용자 질문을 받아 FinFlow RAG를 실행합니다."""
    if not status.data_ready:
        render_data_setup_notice(status)
        return

    if "search_question" not in st.session_state:
        st.session_state.search_question = ""

    if "pending_suggestion" not in st.session_state:
        st.session_state.pending_suggestion = ""

    if "search_result" not in st.session_state:
        st.session_state.search_result = None

    if st.session_state.pending_suggestion:
        st.session_state.search_question = st.session_state.pending_suggestion
        st.session_state.pending_suggestion = ""

    search_col, button_col = st.columns(
        [8, 2],
        gap="small",
        vertical_alignment="bottom",
    )

    with search_col:
        question = st.text_input(
            "금융 질문",
            key="search_question",
            placeholder="금융용어나 시장 상황을 질문해 보세요.",
            label_visibility="collapsed",
        )

    cleaned_question = question.strip()
    is_question_empty = not cleaned_question

    with button_col:
        submitted = st.button(
            "분석하기",
            key="analyze_button",
            type="primary",
            width="stretch",
            disabled=is_question_empty,
        )

    st.markdown(
        """
        <div class="feature-chips">
            <span>HYBRID SEARCH</span>
            <span>FINANCIAL TERMS</span>
            <span>RECENT NEWS</span>
            <span class="feature-chip-primary">GEMINI AI</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    suggested_questions = [
        "이번 주 코스피 변동 원인",
        "금리와 은행주의 관계",
        "환율 상승과 물가",
        "반도체 주가 상승 이유",
    ]

    suggestion_columns = st.columns(4, gap="small")

    for column, suggested_question in zip(
        suggestion_columns,
        suggested_questions,
    ):
        with column:
            if st.button(
                suggested_question,
                key=f"suggestion_{suggested_question}",
                width="stretch",
            ):
                st.session_state["pending_suggestion"] = suggested_question
                st.rerun()

    if submitted:
        cleaned_question = question.strip()

        if not cleaned_question:
            st.warning("질문을 입력해 주세요.")
        else:
            send_event(
                event_type="record_search",
                session_id=st.session_state.session_id,
                page="finflow-search",
                properties={
                    "question_length": len(cleaned_question),
                },
            )

            try:
                with st.spinner(
                    "질문을 분석하고 금융 정보를 검색하고 있습니다."
                ):
                    result = ask_finflow(cleaned_question)

                st.session_state.search_result = result

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

    result = st.session_state.search_result

    if result is not None:
        render_search_results(result)


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
        initial_sidebar_state="expanded",
    )

    load_css()

    pipeline_status = sync_latest_news_snapshot()

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

    selected_page = render_sidebar()

    if selected_page == "AI 금융 검색":
        render_topbar(pipeline_status)
        st.divider()

        render_search(pipeline_status)
        st.write("")

        if st.session_state.get("search_result") is None:
            render_home_statistics(pipeline_status)

    elif selected_page == "시장 브리핑":
        st.title("시장 브리핑")
        st.info("시장 브리핑 페이지는 다음 단계에서 구현합니다.")

    elif selected_page == "데이터 파이프라인":
        render_pipeline(pipeline_status)

    elif selected_page == "프로젝트 정보":
        render_header()
        st.divider()
        render_technology_stack()


if __name__ == "__main__":
    main()
