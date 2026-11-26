from __future__ import annotations

import html
import re
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import streamlit as st

from src.news_snapshot import (
    DATA_SETUP_GUIDE,
    NewsSnapshotStatus,
    sync_latest_news_snapshot,
)
from src.news_retriever import get_news_retention_days
from src.pipeline_status import (
    PipelineHealth,
    PipelineState,
    evaluate_pipeline,
    format_kst,
)
from src.rag import ask_finflow
from utils.event_logger import send_event


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
        st.markdown(
            '<div class="ff-brand">FinFlow</div>'
            '<div class="ff-brand-sub">금융 개념과 최신 뉴스를 함께</div>',
            unsafe_allow_html=True,
        )

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

        st.button(
            "새 질문",
            icon=":material/add:",
            width="stretch",
            on_click=reset_search,
            key="new_search",
        )

    return selected_page


def format_pipeline_updated_at(status: NewsSnapshotStatus) -> str:
    """최근 데이터 갱신 시각을 한국 시간으로 표시합니다."""
    return format_kst(status.updated_at)


def describe_pipeline_schedule(health: PipelineHealth) -> str:
    """마지막 갱신 시각과 다음 예정 시각을 한 줄로 만듭니다."""
    return (
        f"최근 갱신: {format_kst(health.last_updated)} · "
        f"다음 예정: {format_kst(health.next_scheduled)}"
    )


def render_pipeline_state(health: PipelineHealth) -> None:
    """상태별 배지를 출력합니다. 데이터 없음과 확인 오류를 구분합니다."""
    if health.state is PipelineState.ON_SCHEDULE:
        st.success("정기 갱신 정상 (매일 08·12·18시)")
    elif health.state is PipelineState.DELAYED:
        st.warning(
            f"뉴스 갱신 지연: {format_kst(health.missed_run)} 회차 미반영"
        )
    elif health.state is PipelineState.NO_DATA:
        st.error("검색 데이터 준비 필요")
    elif health.state is PipelineState.CHECK_ERROR:
        st.error("최신 스냅샷 확인 오류 (기존 데이터로 서비스 중)")
    else:
        st.info("로컬 데이터 사용 중 (정기 갱신 대상 아님)")


STATUS_TONES = {
    PipelineState.ON_SCHEDULE: "ok",
    PipelineState.DELAYED: "warn",
    PipelineState.NO_DATA: "error",
    PipelineState.CHECK_ERROR: "error",
    PipelineState.LOCAL: "muted",
}


def describe_pipeline_message(health: PipelineHealth) -> str:
    """상태별 한 줄 설명. 데이터 없음과 확인 오류를 구분합니다."""
    if health.state is PipelineState.ON_SCHEDULE:
        return "매일 08·12·18시에 새 뉴스를 반영해요"
    if health.state is PipelineState.DELAYED:
        return f"{format_kst(health.missed_run)} 회차가 아직 반영되지 않았어요"
    if health.state is PipelineState.NO_DATA:
        return "검색 데이터 준비 필요"
    if health.state is PipelineState.CHECK_ERROR:
        return "최신 데이터를 확인하지 못해 기존 데이터로 답하고 있어요"
    return "로컬 데이터라 정기 갱신 대상이 아니에요"


def render_status_summary(health: PipelineHealth) -> None:
    """갱신 상태 카드. 최근 갱신·다음 예정을 각각 한 줄에 둡니다."""
    tone = STATUS_TONES[health.state]
    detail = (
        f'<p class="ff-status-detail">{html.escape(health.detail)}</p>'
        if health.detail
        else ""
    )

    st.markdown(
        compact_html(f"""
        <div class="ff-status ff-status--{tone}">
            <div class="ff-status-head">
                <span class="ff-dot" aria-hidden="true"></span>
                <span class="ff-status-label">{html.escape(health.label)}</span>
            </div>
            <p class="ff-status-msg">{html.escape(describe_pipeline_message(health))}</p>
            <dl class="ff-status-rows">
                <div><dt>최근 갱신</dt><dd>{format_kst(health.last_updated)}</dd></div>
                <div><dt>다음 예정</dt><dd>{format_kst(health.next_scheduled)}</dd></div>
            </dl>
            {detail}
        </div>
        """),
        unsafe_allow_html=True,
    )


def render_topbar(status: NewsSnapshotStatus) -> None:
    """페이지 제목과 뉴스 갱신 상태를 출력합니다."""
    title_col, status_col = st.columns([3, 2], gap="large")

    with title_col:
        st.markdown(
            compact_html("""
            <section class="ff-hero">
                <p class="ff-eyebrow">AI 금융 검색</p>
                <h1>궁금한 금융 개념을 물어보세요</h1>
                <p class="ff-hero-desc">
                    한국은행 경제금융용어와 최근 금융 뉴스를 함께 찾아
                    쉽게 설명해 드려요.
                </p>
            </section>
            """),
            unsafe_allow_html=True,
        )

    with status_col:
        render_status_summary(evaluate_pipeline(status))


def render_pipeline(status: NewsSnapshotStatus) -> None:
    """FinFlow 데이터 파이프라인 상태를 시각적으로 보여줍니다."""
    st.markdown("## 데이터 파이프라인")
    st.caption(
        "금융용어와 금융뉴스가 수집·검증·임베딩되어 "
        "RAG 검색에 연결되는 전체 흐름입니다."
    )

    status_col, updated_col = st.columns([1, 3])

    health = evaluate_pipeline(status)

    with status_col:
        render_pipeline_state(health)

    with updated_col:
        st.caption(describe_pipeline_schedule(health))

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
                "상태": health.label,
                "최근 스냅샷": format_pipeline_updated_at(status),
                "다음 예정 갱신": format_kst(health.next_scheduled),
                "미반영 회차": format_kst(health.missed_run)
                if health.missed_run
                else "없음",
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


def compact_html(markup: str) -> str:
    """줄바꿈·들여쓰기를 없앤 HTML. Markdown이 들여쓴 줄을 코드 블록으로 보지 않게 합니다.

    줄바꿈은 공백 하나로 바꿔 문장 속 띄어쓰기를 지키고, 태그 사이 공백만 없앱니다.
    """
    one_line = re.sub(r"\s*\n\s*", " ", markup).strip()
    return re.sub(r">\s+<", "><", one_line)


CITED_TERMS_PREFIX = "참고한 금융용어:"
CITED_NEWS_PREFIX = "참고한 뉴스:"


def split_answer_citations(
    answer: str,
    terms: list[str],
    news_titles: list[str],
) -> tuple[str, set[str], set[str]]:
    """답변 끝의 '참고한 금융용어/뉴스' 줄을 본문에서 분리합니다.

    실제 검색 결과의 용어·기사 제목과 일치하는 항목만 인용으로 인정하고,
    하나도 맞지 않는 줄은 정보를 잃지 않도록 본문에 그대로 둡니다.
    """
    body_lines: list[str] = []
    cited_terms: set[str] = set()
    cited_titles: set[str] = set()

    for line in answer.splitlines():
        stripped = line.strip().replace("*", "")

        if stripped.startswith(CITED_TERMS_PREFIX):
            matched = {term for term in terms if term and term in stripped}
            if matched:
                cited_terms |= matched
                continue

        if stripped.startswith(CITED_NEWS_PREFIX):
            matched = {
                title for title in news_titles if title and title in stripped
            }
            if matched:
                cited_titles |= matched
                continue

        body_lines.append(line)

    return "\n".join(body_lines).strip(), cited_terms, cited_titles


def format_published_at(value: str) -> str:
    """기사 발행 시각을 KST 'YYYY.MM.DD HH:MM'으로 표시합니다."""
    text = str(value or "").strip()

    if not text:
        return ""

    try:
        published = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text

    if published.tzinfo is None:
        return text

    return format_kst(published)


def safe_external_url(value: str) -> str:
    """http(s) 링크만 허용합니다. 그 밖의 값은 빈 문자열."""
    url = str(value or "").strip()
    parsed = urlparse(url)

    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url

    return ""


def strip_source_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    """'금융용어: …', '뉴스 내용: …' 같은 저장용 머리말을 화면에서 뺍니다."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    kept = [
        line.split(":", 1)[1].strip()
        if any(line.startswith(prefix) for prefix in prefixes)
        else line
        for line in lines
        if not line.startswith(("금융용어:", "뉴스 제목:"))
    ]
    return " ".join(kept).strip()


def render_answer_html(answer: str, terms: list[str]) -> str:
    """강조된 답변을 문단 단위 HTML로 만듭니다."""
    highlighted = highlight_terms_in_answer(answer=answer, terms=terms)
    paragraphs = [
        re.sub(r"^(?:\s*<br>)+|(?:<br>\s*)+$", "", part).strip()
        for part in re.split(r"(?:<br>\s*){2,}", highlighted)
    ]
    return "".join(f"<p>{part}</p>" for part in paragraphs if part)


def render_search_results(result: Any) -> None:
    """검색 결과: 답변 → 출처(금융용어·뉴스) → 검색 과정 순으로 보여줍니다."""
    query_analysis = extract_query_analysis(result)
    rerank_info = extract_rerank_info(result)
    references = extract_references(result)
    news_sources = extract_news_sources(result)
    answer = extract_answer(result)
    reference_terms = extract_reference_terms(references)
    news_titles = [str(news.get("title", "")).strip() for news in news_sources]
    question = (
        str(result.get("query", "")).strip() if isinstance(result, dict) else ""
    )

    body, cited_terms, cited_titles = split_answer_citations(
        answer=answer,
        terms=reference_terms,
        news_titles=news_titles,
    )

    with st.container(key="answer_card"):
        st.markdown(
            '<p class="ff-eyebrow">AI 답변</p>'
            + (
                f'<h2 class="ff-answer-q">{html.escape(question)}</h2>'
                if question
                else ""
            )
            + '<div class="ai-answer-content">'
            + render_answer_html(body, reference_terms)
            + "</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"금융용어 {len(references)}건 · 뉴스 {len(news_sources)}건을 "
            "찾아 답변에 활용했어요"
        )

    term_tab, news_tab = st.tabs(
        [
            f"금융용어 근거 {len(references)}",
            f"관련 뉴스 {len(news_sources)}",
        ]
    )

    with term_tab:
        if references:
            with st.container(key="term_list"):
                for index, reference in enumerate(references, start=1):
                    render_reference(reference, index, cited_terms)
        else:
            st.info("검색된 금융용어 근거가 없습니다.")

    with news_tab:
        render_news_results(news_sources, cited_titles)

    with (
        st.container(key="process_detail"),
        st.expander("검색 과정 자세히 보기", icon=":material/manage_search:"),
    ):
        render_query_analysis(query_analysis)
        render_rerank_info(rerank_info)
        st.json(
            {
                "reference_count": len(references),
                "news_count": len(news_sources),
                "query_analysis": query_analysis,
                "rerank_info": rerank_info,
            },
            expanded=False,
        )


def render_reference(
    reference: dict[str, Any],
    index: int,
    cited_terms: set[str] | None = None,
) -> None:
    """금융용어 근거 한 건. 카드 안에 상자를 겹치지 않습니다."""
    metadata = reference.get("metadata", {})

    if not isinstance(metadata, dict):
        metadata = {}

    term = str(
        reference.get("term")
        or reference.get("title")
        or metadata.get("term")
        or f"검색 결과 {index}"
    ).strip()

    document = str(
        reference.get("document")
        or reference.get("text")
        or reference.get("content")
        or ""
    )
    description = strip_source_prefix(document, ("설명:",))
    preview = description
    if len(preview) > 160:
        preview = f"{preview[:160].rstrip()}…"

    cited = bool(cited_terms) and term in cited_terms
    badge = '<span class="ff-badge">답변에 사용</span>' if cited else ""

    with st.container(key=f"term_item_{index}"):
        st.markdown(
            compact_html(f"""
            <div class="ff-item-head">
                <span class="ff-item-title">{html.escape(term)}</span>{badge}
            </div>
            <p class="ff-item-meta">한국은행 경제금융용어</p>
            {f'<p class="ff-item-body">{html.escape(preview)}</p>' if preview else ''}
            """),
            unsafe_allow_html=True,
        )

        if len(description) > len(preview.rstrip("…")):
            with st.expander("설명 전체 보기"):
                st.write(description)


def render_news_article(
    article: dict[str, Any],
    index: int,
    cited: bool = False,
) -> str:
    """관련 뉴스 한 건을 목록 항목 HTML로 만듭니다."""
    title = str(
        article.get("title")
        or article.get("news_title")
        or f"관련 뉴스 {index}"
    ).strip()

    summary = strip_source_prefix(
        article.get("summary")
        or article.get("description")
        or article.get("content")
        or "",
        ("뉴스 내용:",),
    )

    published = format_published_at(
        article.get("published_at")
        or article.get("pub_date")
        or article.get("date")
        or ""
    )

    category = str(article.get("category") or article.get("section") or "").strip()

    url = safe_external_url(
        article.get("original_url")
        or article.get("url")
        or article.get("link")
        or article.get("originallink")
        or ""
    )
    source = urlparse(url).netloc.removeprefix("www.") if url else ""

    meta = " · ".join(
        f'<span class="ff-nowrap">{html.escape(part)}</span>'
        for part in (published, category, source)
        if part
    )
    badge = '<span class="ff-badge">답변에 사용</span>' if cited else ""
    title_html = (
        f'<a class="ff-news-title" href="{html.escape(url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer">{html.escape(title)}</a>'
        if url
        else f'<span class="ff-news-title">{html.escape(title)}</span>'
    )

    return compact_html(f"""
        <li class="ff-news-item">
            <div class="ff-item-head">{title_html}{badge}</div>
            {f'<p class="ff-item-meta">{meta}</p>' if meta else ''}
            {f'<p class="ff-item-body ff-clamp">{html.escape(summary)}</p>' if summary else ''}
        </li>
    """)


def render_news_results(
    news_articles: list[dict[str, Any]],
    cited_titles: set[str] | None = None,
) -> None:
    """관련 뉴스를 최대 6건, 한 장의 목록으로 표시합니다."""
    if not news_articles:
        st.info(
            "검색된 관련 뉴스가 없습니다. "
            f"뉴스는 최근 {get_news_retention_days()}일 안에 발행된 기사만 제공합니다."
        )
        return

    cited_titles = cited_titles or set()
    items = "".join(
        render_news_article(
            article=article,
            index=index,
            cited=str(article.get("title", "")).strip() in cited_titles,
        )
        for index, article in enumerate(news_articles[:6], start=1)
    )

    st.markdown(
        f'<ul class="ff-news-list">{items}</ul>',
        unsafe_allow_html=True,
    )


def render_query_analysis(
    query_analysis: dict[str, Any],
) -> None:
    """Gemini가 만든 검색 질의를 표로 보여줍니다(검색 과정 펼침 안에서 사용)."""
    if not query_analysis:
        return

    term_queries = query_analysis.get("financial_term_queries", [])
    keywords = query_analysis.get("keywords", [])
    rows = {
        "질문 의도": query_analysis.get("intent", ""),
        "금융용어 검색어": query_analysis.get("financial_term_query", ""),
        "금융용어 세부 검색어": ", ".join(term_queries)
        if isinstance(term_queries, list)
        else "",
        "뉴스 검색어": query_analysis.get("news_query", ""),
        "핵심 키워드": ", ".join(keywords) if isinstance(keywords, list) else "",
    }

    st.markdown("**질문 분석**")
    for label, value in rows.items():
        if value:
            st.markdown(f"- {label}: {value}")

    if query_analysis.get("used_fallback", False):
        st.caption("질문 분석에 실패하여 원본 질문을 검색어로 사용했습니다.")


def render_rerank_info(
    rerank_info: dict[str, Any],
) -> None:
    """금융용어 재정렬 정보를 보여줍니다(검색 과정 펼침 안에서 사용)."""
    if not rerank_info:
        return

    st.markdown("**금융용어 재정렬**")
    st.markdown(f"- 검색 후보: {rerank_info.get('candidate_count', 0)}개")

    reason = rerank_info.get("reason", "")
    if reason:
        st.markdown(f"- 선택 이유: {reason}")

    if rerank_info.get("used_fallback", False):
        st.caption("재정렬에 실패하여 기존 검색 순서를 사용했습니다.")


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

    # 입력과 제출을 폼으로 묶는다.
    # - 입력 후 바로 클릭해도 첫 클릭이 제출된다 (버튼을 비활성화하지 않음).
    # - 입력창에서 Enter를 눌러도 제출된다.
    # 빈 질문은 제출 후 아래에서 경고로 처리한다.
    with st.form("search_form", border=False, enter_to_submit=True):
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

        with button_col:
            submitted = st.form_submit_button(
                "분석하기",
                key="analyze_button",
                type="primary",
                width="stretch",
            )

    suggested_questions = [
        "이번 주 코스피 변동 원인",
        "금리와 은행주의 관계",
        "환율 상승과 물가",
        "반도체 주가 상승 이유",
    ]

    with st.container(horizontal=True, key="suggestions", gap="small"):
        for suggested_question in suggested_questions:
            if st.button(
                suggested_question,
                key=f"suggestion_{suggested_question}",
                width="content",
            ):
                st.session_state["pending_suggestion"] = suggested_question
                st.rerun()

    st.caption(
        f"뉴스 제공 범위: 최근 {get_news_retention_days()}일 안에 발행된 기사"
    )

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

            # 새 질문을 제출하면 이전 결과부터 비운다.
            # 이 질문이 실패했을 때 이전 질문의 답변·출처가
            # 이 질문의 결과처럼 아래에 남지 않게 한다.
            st.session_state.search_result = None

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
        page_icon=":material/insights:",
        layout="wide",
        initial_sidebar_state="auto",
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
        render_search(pipeline_status)

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
