"""평가 CLI의 입력, 자동 판정, 오류 집계를 Gemini 없이 검증한다."""

import json

from scripts.rag import evaluate
from src import rag


def test_default_cli_runs_all_cases_and_continues_after_error(monkeypatch, capsys):
    cases = evaluate.load_cases(evaluate.DEFAULT_CASES)
    assert len(cases) == 18
    by_question = {case["question"]: case for case in cases}
    calls = []

    def ask(question):
        calls.append(question)
        case = by_question[question]
        if case is cases[0]:
            raise RuntimeError("테스트 질문의 실행 오류")
        return {
            "answer": " / ".join(group[0] for group in case["answer_keyword_groups"]),
            "sources": [{"term": term} for term in case.get("expected_terms", [])],
            "news_sources": [{"title": word, "summary": ""} for word in case.get("news_keywords", [])],
            "query_analysis": {"used_fallback": False},
            "rerank_info": {"used_fallback": False},
        }

    monkeypatch.setattr(rag, "ask_finflow", ask)
    monkeypatch.setattr("sys.argv", ["evaluate.py"])
    assert evaluate.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert calls == list(by_question)
    assert report["summary"]["errors"] == 1 and report["summary"]["passed"] == 17
    assert [item["case"] for item in report["cases"]] == cases


def test_scoring_reports_news_misses_independently_from_lexical_answer_match():
    case = {
        "expected_terms": ["기준 금리"],
        "answer_keyword_groups": [["한국은행", "한은"], ["인상"]],
        "news_keywords": ["금통위"],
    }
    result = {
        "answer": "한은이 금리를 인상했다는 보도입니다.",
        "sources": [{"term": "통화정책"}, {"term": "기준금리"}],
        "news_sources": [{"title": "해외 증시 동향", "summary": ""}],
    }
    score = evaluate.score_case(case, result)
    assert score["term_recall"] == score["answer_keyword_coverage"] == 1.0
    assert score["term_reciprocal_rank"] == 0.5
    assert score["news_topic_hit"] is score["passed"] is False
