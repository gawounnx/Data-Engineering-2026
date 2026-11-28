"""앱과 같은 RAG 진입점으로 평가 질문을 실행해 JSON 보고서를 출력한다.

프로젝트 루트에서 python -m scripts.rag.evaluate 를 실행한다.
용어는 재정렬 후 목록에서 이름을 비교한다. 답변은 각 키워드 그룹에서
하나 이상, 뉴스는 한 기사 제목 또는 요약에서 주제어 하나 이상을 찾는다.
답변 점수는 기대 키워드 그룹의 포함률이다.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any


DEFAULT_CASES = Path(__file__).with_name("evaluation_cases.json")


def normalize(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold()
        if char.isalnum()
    )


def validate_keywords(values: Any, field: str) -> None:
    if not isinstance(values, list) or not all(
        isinstance(value, str) and normalize(value) for value in values
    ):
        raise ValueError(f"{field}에 유효한 문자열 목록이 필요합니다.")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("평가 파일에 질문이 하나 이상 있어야 합니다.")

    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"{index}번째 케이스는 JSON 객체여야 합니다.")
        case_id = case.get("id")
        question = case.get("question")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen_ids:
            raise ValueError(f"{index}번째 케이스에 고유한 id가 필요합니다.")
        seen_ids.add(case_id)
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"{case_id} 케이스에 질문이 필요합니다.")

        terms = case.get("expected_terms", [])
        groups = case.get("answer_keyword_groups", [])
        news_keywords = case.get("news_keywords", [])
        validate_keywords(terms, f"{case_id}.expected_terms")
        validate_keywords(news_keywords, f"{case_id}.news_keywords")
        if not isinstance(groups, list) or any(not group for group in groups):
            raise ValueError(
                f"{case_id}.answer_keyword_groups에 비어 있지 않은 그룹 목록이 필요합니다."
            )
        for group in groups:
            validate_keywords(group, f"{case_id}.answer_keyword_groups")
        if not (terms or groups or news_keywords):
            raise ValueError(f"{case_id} 케이스에 채점 기준이 필요합니다.")
    return cases


def score_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    terms = case.get("expected_terms", [])
    groups = case.get("answer_keyword_groups", [])
    news_keywords = case.get("news_keywords", [])

    expected = {normalize(term) for term in terms}
    ranked_terms = [normalize(source["term"]) for source in result["sources"]]
    matched_terms = expected.intersection(ranked_terms)
    first_hit = next(
        (rank for rank, term in enumerate(ranked_terms, start=1) if term in expected),
        None,
    )
    answer = normalize(result["answer"])
    missing_groups = [
        group for group in groups
        if not any(normalize(keyword) in answer for keyword in group)
    ]
    normalized_news_keywords = [normalize(keyword) for keyword in news_keywords]
    matching_news_ranks = [
        rank for rank, article in enumerate(result["news_sources"], start=1)
        if any(
            keyword in normalize(article[field])
            for field in ("title", "summary")
            for keyword in normalized_news_keywords
        )
    ]

    passed = (
        bool(answer)
        and matched_terms == expected
        and not missing_groups
        and (not news_keywords or bool(matching_news_ranks))
    )
    return {
        "passed": passed,
        "term_recall": len(matched_terms) / len(expected) if expected else None,
        "term_reciprocal_rank": (1 / first_hit if first_hit else 0.0) if expected else None,
        "missing_terms": [term for term in terms if normalize(term) not in matched_terms],
        "answer_keyword_coverage": 1 - len(missing_groups) / len(groups) if groups else None,
        "missing_answer_keyword_groups": missing_groups,
        "news_topic_hit": bool(matching_news_ranks) if news_keywords else None,
        "matching_news_ranks": matching_news_ranks,
    }


def evaluate(
    cases: list[dict[str, Any]],
    ask: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    reports = []
    for case in cases:
        started = perf_counter()
        try:
            result = ask(case["question"])
        except Exception as error:
            reports.append({
                "case": case,
                "status": "error",
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "error": {"type": type(error).__name__, "message": str(error)},
            })
            continue

        latency_ms = round((perf_counter() - started) * 1000, 1)
        scores = score_case(case, result)
        reports.append({
            "case": case,
            "status": "passed" if scores["passed"] else "failed",
            "latency_ms": latency_ms,
            "scores": scores,
            "result": result,
        })

    completed = [report for report in reports if report["status"] != "error"]
    summary = {
        "total": len(reports),
        "passed": sum(report["status"] == "passed" for report in reports),
        "failed": sum(report["status"] == "failed" for report in reports),
        "errors": len(reports) - len(completed),
        "query_analysis_fallbacks": sum(
            report["result"]["query_analysis"]["used_fallback"] for report in completed
        ),
        "rerank_fallbacks": sum(
            report["result"]["rerank_info"]["used_fallback"] for report in completed
        ),
    }
    for metric in (
        "term_recall",
        "term_reciprocal_rank",
        "answer_keyword_coverage",
        "news_topic_hit",
    ):
        values = [
            report["scores"][metric] for report in completed
            if report["scores"][metric] is not None
        ]
        summary[f"mean_{metric}"] = round(mean(values), 4) if values else None

    return {"summary": summary, "cases": reports}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="앱의 RAG 기본값으로 질문을 평가하고 JSON 보고서를 표준 출력에 기록합니다.",
        epilog="종료 코드 0은 전체 통과, 1은 평가 실패 또는 실행 오류, 2는 입력 오류입니다.",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="평가 질문 JSON 경로")
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as error:
        parser.error(f"평가 파일 {args.cases} 읽기 실패 ({error})")

    with redirect_stdout(sys.stderr):
        from src.rag import ask_finflow

        report = evaluate(cases, ask_finflow)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    summary = report["summary"]
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
