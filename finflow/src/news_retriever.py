"""금융뉴스를 키워드·벡터·최신성 기준으로 하이브리드 검색한다."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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

# 뉴스 파이프라인(load_news_to_chromadb.py)이 남기는 보관 기간과 같은 값을 쓴다.
DEFAULT_NEWS_RETENTION_DAYS = 7

# "최근 뉴스" 같은 질문에서 최신 기사가 키워드 점수에 밀리지 않도록 하는 설정.
# 키워드 1개당 최대 8점(제목 5 + 요약 2 + 수집어 1)이라 키워드가 많을수록 점수 차가
# 커지므로, 최신 가산점 상한도 키워드 수에 비례해 키운다.
RECENCY_INTENT_KEYWORDS = (
    "최근", "최신", "요즘", "근래", "방금", "오늘", "어제", "현재", "이번주", "금주",
)
RECENT_INTENT_BONUS_PER_KEYWORD = 2.0
RECENT_INTENT_MIN_BONUS = 6.0
RECENT_INTENT_HALF_LIFE_DAYS = 1.5


def has_recency_intent(query: str) -> bool:
    """질문이 최신 소식을 원하는지(최근·오늘 등) 판단한다."""
    normalized = str(query or "").replace(" ", "")
    return any(keyword in normalized for keyword in RECENCY_INTENT_KEYWORDS)


def get_news_retention_days() -> int:
    """뉴스 제공 범위(일). NEWS_RETENTION_DAYS가 비었거나 잘못되면 기본값."""
    raw_value = os.getenv("NEWS_RETENTION_DAYS", "").strip()

    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_NEWS_RETENTION_DAYS

    return value if value >= 1 else DEFAULT_NEWS_RETENTION_DAYS


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

KOREA_TIME = ZoneInfo("Asia/Seoul")
KOREAN_POLICY_ENTITY = re.compile(r"(?<![가-힣])(?:한국은행|한은|금통위|금융통화위원회)")
RATE_OUTCOME_WORDS = ("인상", "인하", "동결", "유지")
RATE_DECISION_WORDS = (*RATE_OUTCOME_WORDS, "결정", "발표", "정했", "정한", "올렸", "내렸")
FORECAST_WORDS = (
    "전망", "예상", "예고", "가능성", "할까", "무게", "여부", "예정", "촉각",
    "검토", "유력", "앞두고",
)
UNCONFIRMED_OUTCOME = re.compile(
    r"미확정|사실무근|확정(?:이|은|된(?:것은|건|게))?(?:아냐|아니|되지|안됐)"
)
EXCHANGE_RATE_MOVEMENTS = (
    "상승", "하락", "아래", "위로", "떨어", "내려", "올라", "올랐", "내렸",
    "반등", "보합", "마감",
)
POLICY_SUBJECT_SUFFIX = r"(?:의|가|이|는|은)?(?:(?:금통위|금융통화위원회)(?:의|가|는)?)?"
RATE_DECISION_HEADLINE = re.compile(
    POLICY_SUBJECT_SUFFIX + r"(?:기준|정책)금리(?:를|는)?(?:연)?(?:\d+(?:로|에서)?)?"
    r"(?:인상|인하|동결|유지)(?!하지)"
)
FX_POLICY_HEADLINE = re.compile(
    POLICY_SUBJECT_SUFFIX + r"(?:기준|정책)?금리(?:결정|발표|인상|인하|동결|유지)(?:직후|이후|후|뒤|에|여파)"
)

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
    """뉴스 검색 범위(일). 질문의 기간 표현을 따르되 제공 범위를 넘지 않는다."""
    return min(
        infer_requested_days(query),
        get_news_retention_days(),
    )


def infer_requested_days(
    query: str,
) -> int:
    """질문의 기간 표현에 따라 요청 기간(일)을 추정한다."""
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
    prefer_recent: bool = False,
    keyword_count: int = 0,
) -> float:
    """검색 기간 안에서 최신 기사에 가점을 준다.

    기본: 0~3점, 기간에 걸쳐 선형 감소.
    prefer_recent(최근 소식 질문): 상한을 키워드 수에 비례해 키우고
    RECENT_INTENT_HALF_LIFE_DAYS마다 절반으로 줄여, 같은 주제의 더 새 기사를 앞세운다.
    """
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

    if prefer_recent:
        max_bonus = max(
            RECENT_INTENT_MIN_BONUS,
            RECENT_INTENT_BONUS_PER_KEYWORD * keyword_count,
        )
        return max_bonus * 0.5 ** (age_days / RECENT_INTENT_HALF_LIFE_DAYS)

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
    newest_first: bool = False,
    *,
    priority_article_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """JSON 뉴스에서 키워드 일치 후보를 찾는다.

    newest_first=True면 키워드가 하나라도 맞는 기사 중 최신순으로 고른다.
    """
    candidates: list[dict[str, Any]] = []

    for article in articles:
        score = calculate_keyword_score(
            article=article,
            keywords=keywords,
        )

        if score <= 0 and str(article.get("article_id")) not in priority_article_ids:
            continue

        candidates.append(
            {
                "article": article,
                "keyword_score": score,
            }
        )

    if newest_first:
        candidates.sort(
            key=lambda item: (
                parse_published_at(
                    item["article"].get(
                        "published_at"
                    )
                ),
                item["keyword_score"],
            ),
            reverse=True,
        )
    else:
        candidates.sort(
            key=lambda item: (
                str(item["article"].get("article_id")) in priority_article_ids,
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


def requested_news_day(question: str) -> date | None:
    """질문에 날짜가 있으면 한국시간 기준의 날짜로 해석한다."""
    numeric = re.search(r"(?<!\d)(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)", question)
    if numeric:
        return date(*map(int, numeric.groups()))

    today = datetime.now(KOREA_TIME).date()
    korean = re.search(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일", question)
    if korean:
        year, month, day = korean.groups()
        return date(int(year) if year else today.year, int(month), int(day))
    for word, offset in (("어제", -1), ("오늘", 0), ("내일", 1)):
        if word in question:
            return today + timedelta(days=offset)
    return None


def find_event_evidence(
    articles: list[dict[str, Any]],
    question: str,
    search_days: int,
) -> frozenset[str]:
    """금통위 결정과 직후 환율 관측 질문에 직접 답하는 기사 ID를 찾는다."""
    question_text = re.sub(r"[^가-힣0-9]", "", question)
    if (
        not KOREAN_POLICY_ENTITY.search(question)
        or "금리" not in question_text
        or not any(word in question_text for word in RATE_DECISION_WORDS)
        or any(word in question_text for word in FORECAST_WORDS)
        or re.search(r"다음(?:번)?(?:금통위|금융통화위원회|회의|기준금리|정책금리|결정|한국은행)|향후", question_text)
    ):
        return frozenset()

    exchange_rate = "원달러" in question_text and "환율" in question_text
    if not exchange_rate and (
        not any(word in question_text for word in ("기준금리", "정책금리"))
        or "환율" in question_text
    ):
        return frozenset()

    try:
        requested_day = requested_news_day(question)
    except ValueError:
        return frozenset()
    if exchange_rate and (
        any(word in question_text for word in ("직전", "결정전", "발표전"))
        or (
            requested_day is None
            and not any(word in question_text for word in ("직후", "이후", "당시"))
        )
    ):
        return frozenset()

    matched_ids = set()
    for article in articles:
        article_id = article.get("article_id")
        title = str(article.get("title", ""))
        headline, *rest = re.split(r"[…⋯]|[.·]{2,}", title, maxsplit=1)
        compact_headline = re.sub(r"[^가-힣0-9]", "", headline)
        continuation = re.sub(r"[^가-힣0-9]", "", rest[0]) if rest else ""
        if (
            not article_id
            or "?" in headline
            or "？" in headline
            or any(word in compact_headline for word in FORECAST_WORDS)
            or continuation.startswith(FORECAST_WORDS)
            or UNCONFIRMED_OUTCOME.search(compact_headline)
            or UNCONFIRMED_OUTCOME.search(continuation)
        ):
            continue
        entity = KOREAN_POLICY_ENTITY.search(headline)
        if not entity:
            continue
        policy_headline = re.sub(r"[^가-힣0-9]", "", headline[entity.end():])
        if exchange_rate:
            if (
                "원달러환율" not in compact_headline
                or not FX_POLICY_HEADLINE.match(policy_headline)
                or not any(word in compact_headline for word in EXCHANGE_RATE_MOVEMENTS)
            ):
                continue
        elif not RATE_DECISION_HEADLINE.match(policy_headline):
            continue
        elif not (
            re.search(r"\d+(?:\.\d+)?\s*%", headline)
            or any(
                f"{word}{ending}" in str(article.get("summary", ""))
                for word in RATE_OUTCOME_WORDS
                for ending in ("했다", "하였다", "하기로")
            )
        ):
            continue

        published_at = article.get("published_at")
        if requested_day and parse_published_at(published_at).astimezone(KOREA_TIME).date() != requested_day:
            continue
        if is_excluded_article(title) or calculate_recency_score(published_at, search_days) < 0:
            continue
        matched_ids.add(str(article_id))
    return frozenset(matched_ids)


def search_financial_news(
    query: str,
    top_k: int = 5,
    news_path: str | Path = DEFAULT_NEWS_PATH,
    prefer_recent: bool | None = None,
    *,
    original_query: str | None = None,
) -> list[dict[str, Any]]:
    """금융뉴스를 하이브리드 방식으로 검색한다.

    prefer_recent가 None이면 query의 표현(최근·오늘 등)으로 판단한다.
    original_query가 주어지면 사용자가 명시한 사건과 날짜를 근거 선택에 사용한다.
    """
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
    priority_article_ids = (
        find_event_evidence(articles, original_query, search_days)
        if original_query else frozenset()
    )

    vector_candidates = search_vector_candidates(
        query=clean_query,
        candidate_count=VECTOR_CANDIDATE_COUNT,
    )

    keyword_candidates = search_keyword_candidates(
        articles=articles,
        keywords=keywords,
        candidate_count=VECTOR_CANDIDATE_COUNT,
        priority_article_ids=priority_article_ids,
    )

    if prefer_recent is None:
        prefer_recent = has_recency_intent(clean_query)

    if prefer_recent:
        # 키워드 점수 순 후보만으로는 새 기사가 빠질 수 있어 최신순 후보를 더한다.
        keyword_candidates = keyword_candidates + search_keyword_candidates(
            articles=articles,
            keywords=keywords,
            candidate_count=VECTOR_CANDIDATE_COUNT,
            newest_first=True,
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
            prefer_recent=prefer_recent,
            keyword_count=len(keywords),
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
            prefer_recent=prefer_recent,
            keyword_count=len(keywords),
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

    # 직접 근거 두 건까지 먼저 보여주고, 나머지는 기존 점수순을 유지한다.
    evidence = [
        article for article in scored_articles
        if article["article_id"] in priority_article_ids
    ][:min(2, top_k)]
    selected_ids = {article["article_id"] for article in evidence}
    remaining = [
        article for article in scored_articles
        if article["article_id"] not in selected_ids
    ]
    return (evidence + remaining)[:top_k]


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
