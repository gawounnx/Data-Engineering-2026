"""금융뉴스를 키워드·벡터·최신성 기준으로 하이브리드 검색한다."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_NEWS_PATH = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "naver_financial_news.json"
)

CHROMA_PATH = PROJECT_ROOT / "data" / "chroma"
NEWS_COLLECTION_NAME = "financial_news"
MODEL_NAME = "kekeappa/kor-static-embedding-128"


def open_chroma_client() -> Any:
    """현재 CHROMA_PATH의 ChromaDB를 연다.

    빈 폴더에서 PersistentClient를 열면 빈 DB가 새로 생기므로,
    DB 파일이 없으면 데이터 준비 방법과 함께 오류를 낸다.
    """
    if not (CHROMA_PATH / "chroma.sqlite3").exists():
        raise FileNotFoundError(
            f"ChromaDB 데이터가 없습니다: {CHROMA_PATH}. "
            "finflow/README.md의 '데이터 준비'에 따라 "
            "스냅샷을 내려받으세요."
        )

    return chromadb.PersistentClient(
        path=str(CHROMA_PATH)
    )

VECTOR_CANDIDATE_COUNT = 30

STOPWORDS = {
    "요즘",
    "최근",
    "현재",
    "오늘",
    "어제",
    "이번",
    "금주",
    "이번주",
    "왜",
    "어떤",
    "어떻게",
    "알려줘",
    "설명해줘",
    "뉴스",
    "기사",
    "대한",
    "관련",
    "것",
    "이유",
    "동향",
    "전망",
    "변동",
    "변동한",
    "변화",
    "변화한",
    "영향",
    "미친",
    "분석",
}

EXCLUDED_TITLE_PATTERNS = [
    r"^\[(포토|mhn포토)\]",
    r"월드컵",
    r"영화",
    r"드라마",
    r"배우",
    r"가수",
    r"연예",
    r"축구",
    r"야구",
    r"골프",
]


@lru_cache(maxsize=1)
def load_news_articles(
    news_path: str | Path = DEFAULT_NEWS_PATH,
) -> list[dict[str, Any]]:
    """뉴스 JSON을 읽고, 파일이 없으면 ChromaDB에서 복원한다."""
    path = Path(news_path)

    if path.exists():
        try:
            with path.open(
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"뉴스 JSON 형식이 올바르지 않습니다: {path}"
            ) from exc

        articles = data.get("articles", [])

        if not isinstance(articles, list):
            raise ValueError(
                "JSON의 articles 값은 리스트여야 합니다."
            )

        return articles

    print(
        "[정보] 로컬 뉴스 JSON이 없어 "
        "ChromaDB에서 뉴스 데이터를 불러옵니다."
    )

    client = open_chroma_client()

    collection = client.get_collection(
        name=NEWS_COLLECTION_NAME
    )

    result = collection.get(
        include=[
            "documents",
            "metadatas",
        ]
    )

    ids = result.get("ids", [])
    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])

    articles: list[dict[str, Any]] = []
    seen_article_ids: set[str] = set()

    for chunk_id, document, metadata in zip(
        ids,
        documents,
        metadatas,
        strict=True,
    ):
        metadata = metadata or {}

        article_id = str(
            metadata.get(
                "article_id",
                chunk_id,
            )
        )

        if article_id in seen_article_ids:
            continue

        articles.append(
            {
                "article_id": article_id,
                "title": metadata.get(
                    "title",
                    "제목 없음",
                ),
                "summary": document or "",
                "published_at": metadata.get(
                    "published_at",
                    "",
                ),
                "collected_at": metadata.get(
                    "collected_at",
                    "",
                ),
                "category": metadata.get(
                    "category",
                    "미분류",
                ),
                "original_url": metadata.get(
                    "original_url",
                    "",
                ),
                "naver_url": metadata.get(
                    "naver_url",
                    "",
                ),
                "financial_relevance_score": metadata.get(
                    "financial_relevance_score",
                    0,
                ),
                "source_type": metadata.get(
                    "source_type",
                    "chromadb",
                ),
                "search_queries": [],
            }
        )

        seen_article_ids.add(article_id)

    if not articles:
        raise ValueError(
            "ChromaDB에서 뉴스 데이터를 찾지 못했습니다."
        )

    return articles


def is_excluded_article(
    title: str,
) -> bool:
    """검색 품질을 떨어뜨리는 명백한 비금융 기사를 제외한다."""
    return any(
        re.search(
            pattern,
            title,
            flags=re.IGNORECASE,
        )
        for pattern in EXCLUDED_TITLE_PATTERNS
    )


def normalize_korean_keyword(
    word: str,
) -> str:
    """간단한 한국어 조사와 어미를 제거한다."""
    suffixes = [
        "으로부터",
        "에게서",
        "에서는",
        "으로는",
        "이라는",
        "이라고",
        "에서",
        "에게",
        "한테",
        "으로",
        "로는",
        "에는",
        "부터",
        "까지",
        "보다",
        "처럼",
        "하고",
        "이며",
        "이고",
        "이나",
        "거나",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "에",
        "의",
        "와",
        "과",
        "도",
        "로",
    ]

    normalized = word.strip().lower()

    for suffix in suffixes:
        if (
            normalized.endswith(suffix)
            and len(normalized) - len(suffix) >= 2
        ):
            return normalized[:-len(suffix)]

    return normalized


def extract_query_keywords(
    query: str,
) -> list[str]:
    """질문에서 검색 핵심어를 추출한다."""
    words = re.findall(
        r"[가-힣A-Za-z0-9]+",
        query.lower(),
    )

    keywords: list[str] = []

    for word in words:
        normalized = normalize_korean_keyword(
            word
        )

        if len(normalized) < 2:
            continue

        if normalized in STOPWORDS:
            continue

        keywords.append(normalized)

    return list(dict.fromkeys(keywords))


def infer_search_days(
    query: str,
) -> int:
    """질문의 기간 표현에 따라 뉴스 검색 범위를 정한다."""
    normalized = query.lower().replace(" ", "")

    explicit_patterns = [
        (r"(\d+)일", 1),
        (r"(\d+)주", 7),
        (r"(\d+)개월", 30),
        (r"(\d+)년", 365),
    ]

    for pattern, multiplier in explicit_patterns:
        match = re.search(pattern, normalized)

        if match:
            value = int(match.group(1))

            # 비교를 위해 요청 기간보다 조금 넓게 검색한다.
            return min(
                max(value * multiplier * 2, 3),
                365,
            )

    if any(
        keyword in normalized
        for keyword in ["오늘", "어제", "현재"]
    ):
        return 3

    if any(
        keyword in normalized
        for keyword in ["이번주", "금주"]
    ):
        return 14

    if any(
        keyword in normalized
        for keyword in ["이번달", "금월"]
    ):
        return 45

    if "올해" in normalized:
        return 365

    return 30


def parse_published_at(
    value: str | None,
) -> datetime:
    """날짜 문자열을 UTC datetime으로 변환한다."""
    if not value:
        return datetime.min.replace(
            tzinfo=timezone.utc
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    except ValueError:
        return datetime.min.replace(
            tzinfo=timezone.utc
        )


def calculate_keyword_score(
    article: dict[str, Any],
    keywords: list[str],
) -> float:
    """제목·요약·수집 검색어의 키워드 일치 점수를 계산한다."""
    title = str(
        article.get("title", "")
    ).lower()

    summary = str(
        article.get("summary", "")
    ).lower()

    search_queries = article.get(
        "search_queries",
        [],
    )

    if not isinstance(search_queries, list):
        search_queries = []

    search_query_text = " ".join(
        map(str, search_queries)
    ).lower()

    score = 0.0

    for keyword in keywords:
        if keyword in title:
            score += 5.0

        if keyword in summary:
            score += 2.0

        if keyword in search_query_text:
            score += 1.0

    return score


def calculate_recency_score(
    published_at: str | None,
    search_days: int,
) -> float:
    """검색 기간 안에서 최신 기사에 가점을 준다."""
    published = parse_published_at(
        published_at
    )

    if published.year <= 1:
        return 0.0

    now = datetime.now(
        timezone.utc
    )

    age_days = max(
        (now - published).total_seconds()
        / 86400,
        0.0,
    )

    if age_days > search_days:
        return -100.0

    return max(
        0.0,
        3.0 * (
            1.0 - age_days / search_days
        ),
    )


@lru_cache(maxsize=1)
def load_embedding_model() -> SentenceTransformer:
    """뉴스 질문 임베딩 모델을 캐시해 불러온다."""
    return SentenceTransformer(
        model_name_or_path=MODEL_NAME,
        device="cpu",
    )


def search_vector_candidates(
    query: str,
    candidate_count: int,
) -> list[dict[str, Any]]:
    """ChromaDB에서 의미가 유사한 뉴스 후보를 검색한다."""
    client = open_chroma_client()

    collection = client.get_collection(
        name=NEWS_COLLECTION_NAME
    )

    model = load_embedding_model()

    query_embedding = model.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True,
    )[0].astype(float).tolist()

    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(
            candidate_count,
            collection.count(),
        ),
        include=[
            "documents",
            "metadatas",
            "distances",
        ],
    )

    ids = result.get("ids", [[]])[0]
    documents = result.get(
        "documents",
        [[]],
    )[0]
    metadatas = result.get(
        "metadatas",
        [[]],
    )[0]
    distances = result.get(
        "distances",
        [[]],
    )[0]

    candidates: list[dict[str, Any]] = []

    for chunk_id, document, metadata, distance in zip(
        ids,
        documents,
        metadatas,
        distances,
        strict=True,
    ):
        similarity = max(
            0.0,
            1.0 - float(distance),
        )

        candidates.append(
            {
                "chunk_id": chunk_id,
                "document": document,
                "metadata": metadata,
                "vector_similarity": similarity,
            }
        )

    return candidates


def search_keyword_candidates(
    articles: list[dict[str, Any]],
    keywords: list[str],
    candidate_count: int = 30,
) -> list[dict[str, Any]]:
    """JSON 뉴스에서 키워드 일치 후보를 찾는다."""
    candidates: list[dict[str, Any]] = []

    for article in articles:
        score = calculate_keyword_score(
            article=article,
            keywords=keywords,
        )

        if score <= 0:
            continue

        candidates.append(
            {
                "article": article,
                "keyword_score": score,
            }
        )

    candidates.sort(
        key=lambda item: (
            item["keyword_score"],
            parse_published_at(
                item["article"].get(
                    "published_at"
                )
            ),
        ),
        reverse=True,
    )

    return candidates[:candidate_count]


def search_financial_news(
    query: str,
    top_k: int = 5,
    news_path: str | Path = DEFAULT_NEWS_PATH,
) -> list[dict[str, Any]]:
    """금융뉴스를 하이브리드 방식으로 검색한다."""
    clean_query = query.strip()

    if not clean_query:
        raise ValueError(
            "검색 질문이 비어 있습니다."
        )

    if top_k <= 0:
        raise ValueError(
            "top_k는 1 이상이어야 합니다."
        )

    articles = load_news_articles(
        news_path
    )

    article_by_id = {
        str(article.get("article_id")): article
        for article in articles
        if article.get("article_id")
    }

    keywords = extract_query_keywords(
        clean_query
    )

    search_days = infer_search_days(
        clean_query
    )

    vector_candidates = search_vector_candidates(
        query=clean_query,
        candidate_count=VECTOR_CANDIDATE_COUNT,
    )

    keyword_candidates = search_keyword_candidates(
        articles=articles,
        keywords=keywords,
        candidate_count=VECTOR_CANDIDATE_COUNT,
    )

    scored_articles: list[dict[str, Any]] = []
    seen_article_ids: set[str] = set()

    for candidate in vector_candidates:
        metadata = candidate["metadata"]

        article_id = str(
            metadata.get("article_id", "")
        )

        if not article_id:
            continue

        if article_id in seen_article_ids:
            continue

        published_at = str(
            metadata.get("published_at", "")
        )

        recency_score = calculate_recency_score(
            published_at=published_at,
            search_days=search_days,
        )

        if recency_score < 0:
            continue

        raw_article = article_by_id.get(
            article_id,
            {},
        )

        title = str(
            metadata.get(
                "title",
                raw_article.get(
                    "title",
                    "제목 없음",
                ),
            )
        )

        if is_excluded_article(title):
            continue

        summary = str(
            raw_article.get(
                "summary",
                candidate["document"],
            )
        )

        keyword_article = {
            **raw_article,
            "title": title,
            "summary": summary,
        }

        keyword_score = calculate_keyword_score(
            article=keyword_article,
            keywords=keywords,
        )

        relevance_score = float(
            metadata.get(
                "financial_relevance_score",
                0,
            )
            or 0
        )

        vector_similarity = float(
            candidate["vector_similarity"]
        )

        if vector_similarity < 0.25:
            continue

        keyword_penalty = (
            -3.0
            if keywords and keyword_score == 0
            else 0.0
        )

        hybrid_score = (
            vector_similarity * 10.0
            + keyword_score
            + relevance_score * 0.5
            + recency_score
            + keyword_penalty
        )

        result = {
            **raw_article,
            "article_id": article_id,
            "title": title,
            "summary": summary,
            "published_at": published_at,
            "category": metadata.get(
                "category",
                raw_article.get(
                    "category",
                    "미분류",
                ),
            ),
            "original_url": metadata.get(
                "original_url",
                raw_article.get(
                    "original_url",
                    "",
                ),
            ),
            "vector_similarity": round(
                vector_similarity,
                4,
            ),
            "keyword_score": round(
                keyword_score,
                4,
            ),
            "recency_score": round(
                recency_score,
                4,
            ),
            "retrieval_score": round(
                hybrid_score,
                4,
            ),
            "search_days": search_days,
        }

        scored_articles.append(result)
        seen_article_ids.add(article_id)

    for candidate in keyword_candidates:
        raw_article = candidate["article"]

        article_id = str(
            raw_article.get("article_id", "")
        )

        if not article_id:
            continue

        if article_id in seen_article_ids:
            continue

        title = str(
            raw_article.get(
                "title",
                "제목 없음",
            )
        )

        if is_excluded_article(title):
            continue

        published_at = str(
            raw_article.get(
                "published_at",
                "",
            )
        )

        recency_score = calculate_recency_score(
            published_at=published_at,
            search_days=search_days,
        )

        if recency_score < 0:
            continue

        keyword_score = float(
            candidate["keyword_score"]
        )

        relevance_score = float(
            raw_article.get(
                "financial_relevance_score",
                0,
            )
            or 0
        )

        hybrid_score = (
            keyword_score
            + relevance_score * 0.5
            + recency_score
        )

        result = {
            **raw_article,
            "article_id": article_id,
            "title": title,
            "summary": str(
                raw_article.get("summary", "")
            ),
            "published_at": published_at,
            "category": raw_article.get(
                "category",
                "미분류",
            ),
            "original_url": raw_article.get(
                "original_url",
                "",
            ),
            "vector_similarity": 0.0,
            "keyword_score": round(
                keyword_score,
                4,
            ),
            "recency_score": round(
                recency_score,
                4,
            ),
            "retrieval_score": round(
                hybrid_score,
                4,
            ),
            "search_days": search_days,
        }

        scored_articles.append(result)
        seen_article_ids.add(article_id)

    scored_articles.sort(
        key=lambda article: (
            article.get(
                "retrieval_score",
                0,
            ),
            parse_published_at(
                article.get("published_at")
            ),
        ),
        reverse=True,
    )

    return scored_articles[:top_k]


def format_news_context(
    articles: list[dict[str, Any]],
) -> str:
    """검색된 뉴스 목록을 Gemini 프롬프트용 문자열로 변환한다."""
    if not articles:
        return "검색된 관련 뉴스가 없습니다."

    contexts: list[str] = []

    for index, article in enumerate(
        articles,
        start=1,
    ):
        title = article.get(
            "title",
            "제목 없음",
        )
        summary = article.get(
            "summary",
            "요약 없음",
        )
        published_at = article.get(
            "published_at",
            "날짜 없음",
        )
        category = article.get(
            "category",
            "미분류",
        )
        original_url = article.get(
            "original_url",
            "",
        )

        context = (
            f"[뉴스 {index}]\n"
            f"제목: {title}\n"
            f"발행일: {published_at}\n"
            f"카테고리: {category}\n"
            f"요약: {summary}\n"
            f"원문 URL: {original_url}"
        )

        contexts.append(context)

    return "\n\n".join(contexts)