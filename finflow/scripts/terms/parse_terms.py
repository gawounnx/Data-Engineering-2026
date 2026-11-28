"""[2/7] 페이지 텍스트에서 금융용어·연관검색어·설명을 구조화한다.

week1 parse_financial_terms_v3.py를 옮긴 것이다. 처리 로직은 바꾸지 않았다.
(같은 입력으로 기존 financial_terms_800_v3.json을 그대로 재현함을 확인)

입력: data/terms/processed/financial_terms_pages.json
출력: financial_terms_800_v3.json, financial_terms_parse_report_v3.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import terms_config


INPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_pages.json"
)

OUTPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_800_v3.json"
)

REPORT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_parse_report_v3.json"
)

INDEX_START_PAGE = 4
INDEX_END_PAGE = 18

BODY_START_PAGE = 19
BODY_END_PAGE = 423


def load_pages(path: Path) -> list[dict[str, Any]]:
    """페이지별 PDF 텍스트 JSON을 불러옵니다."""
    if not path.exists():
        raise FileNotFoundError(
            f"입력 파일을 찾을 수 없습니다: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    pages = data.get("pages")

    if not isinstance(pages, list):
        raise ValueError(
            "입력 JSON에서 pages 목록을 찾지 못했습니다."
        )

    return pages


def normalize_text(text: str) -> str:
    """공백과 PDF 특수문자를 정리합니다."""
    text = text.replace("\u00a0", " ")
    text = text.replace("ㆍ", "·")
    text = re.sub(r"\s+", " ", text)

    return text.strip()

def normalize_term_key(term: str) -> str:
    """용어 비교를 위한 정규화 키를 만듭니다."""
    term = normalize_text(term)

    term = (
        term.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
    )

    term = re.sub(
        r"\s+[ㄱ-ㅎ]$",
        "",
        term,
    )

    term = term.replace("·", " ")
    term = re.sub(r"\s+", "", term)

    return term.casefold()


def extract_index_terms(
    pages: list[dict[str, Any]],
) -> list[str]:
    """
    PDF 찾아보기 페이지에서 용어명과 페이지 번호를 분리합니다.

    예:
    가계수지··········1
    경기종합지수······13
    """
    extracted_terms: list[str] = []

    pattern = re.compile(
        r"(.+?)(?:·|\.){2,}\s*\d{1,3}"
    )

    for page in pages:
        page_number = page.get("page")
        text = page.get("text", "")

        if not isinstance(page_number, int):
            continue

        if not INDEX_START_PAGE <= page_number <= INDEX_END_PAGE:
            continue

        if not isinstance(text, str):
            continue

        for raw_line in text.splitlines():
            line = normalize_text(raw_line)

            if not line:
                continue

            matches = pattern.findall(line)

            for match in matches:
                term = clean_index_term(match)

                if term:
                    extracted_terms.append(term)

    return remove_duplicate_strings(extracted_terms)


def clean_index_term(term: str) -> str:
    """찾아보기에서 추출한 용어명의 불필요한 문자를 제거합니다."""
    term = normalize_text(term)

    term = re.sub(
        r"^(경제금융용어\s*800선|찾아보기)\s*",
        "",
        term,
    )

    term = re.sub(
        r"^[ㄱ-ㅎ]\s+",
        "",
        term,
    )

    term = re.sub(
        r"^[A-Z]{1,3}\s+",
        "",
        term,
    )

    term = term.strip("·. ")

    if not term:
        return ""

    if len(term) > 100:
        return ""

    if term in {
        "경제금융용어 800선",
        "찾아보기",
    }:
        return ""

    return term


def remove_duplicate_strings(
    values: list[str],
) -> list[str]:
    """문자열 목록의 중복을 순서를 유지하며 제거합니다."""
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        key = normalize_term_key(value)

        if not key or key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def is_page_number(line: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}", line))


def is_section_marker(line: str) -> bool:
    return line in {
        "ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ",
        "ㅅ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ",
        "ㅍ", "ㅎ",
        "A", "B", "C", "ABC",
    }


def is_document_header(line: str) -> bool:
    patterns = (
        r"^[IVX]+\s*경제금융용어\s*800선$",
        r"^경제금융용어\s*800선$",
        r"^찾아보기\s*[IVX]*$",
    )

    return any(
        re.fullmatch(pattern, line)
        for pattern in patterns
    )



def clean_body_lines(
    pages: list[dict[str, Any]],
    term_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    """본문 페이지에서 분석 가능한 줄만 수집합니다."""
    result: list[dict[str, Any]] = []

    for page in pages:
        page_number = page.get("page")
        text = page.get("text", "")

        if not isinstance(page_number, int):
            continue

        if not BODY_START_PAGE <= page_number <= BODY_END_PAGE:
            continue

        if not isinstance(text, str) or not text.strip():
            continue

        lines = [
            normalize_text(line)
            for line in text.splitlines()
            if normalize_text(line)
        ]

        if not lines:
            continue

        # 페이지 첫 줄이 찾아보기의 금융용어이고,
        # 두 번째 줄이 금융용어가 아니면 첫 줄을 러닝 헤더로 판단합니다.
        if len(lines) >= 2:
            first_key = normalize_term_key(lines[0])
            second_key = normalize_term_key(lines[1])

            if (
                first_key in term_lookup
                and second_key not in term_lookup
            ):
                lines = lines[1:]

        for line in lines:
            if is_page_number(line):
                continue

            if is_section_marker(line):
                continue

            if is_document_header(line):
                continue

            result.append(
                {
                    "page": page_number,
                    "text": line,
                }
            )

    return result


def build_term_lookup(
    terms: list[str],
) -> dict[str, str]:
    """정규화된 용어명과 원래 용어명을 연결합니다."""
    lookup: dict[str, str] = {}

    for term in terms:
        key = normalize_term_key(term)

        if key:
            lookup[key] = term

    return lookup


def split_related_terms(text: str) -> list[str]:
    """연관검색어를 목록으로 변환합니다."""
    text = re.sub(
        r"^연관검색어\s*",
        "",
        text,
    ).strip()

    if not text:
        return []

    candidates = re.split(r"[,，]\s*", text)

    result: list[str] = []

    for candidate in candidates:
        candidate = candidate.strip()

        if candidate and candidate not in result:
            result.append(candidate)

    return result


def join_description(lines: list[str]) -> str:
    """여러 줄로 분리된 설명을 하나로 합칩니다."""
    description = " ".join(lines)

    description = re.sub(
        r"\s+",
        " ",
        description,
    )

    description = re.sub(
        r"\s+([,.!?%])",
        r"\1",
        description,
    )

    description = re.sub(
        r"\(\s+",
        "(",
        description,
    )

    description = re.sub(
        r"\s+\)",
        ")",
        description,
    )

    return description.strip()


def parse_terms(
    lines: list[dict[str, Any]],
    term_lookup: dict[str, str],
) -> tuple[
    list[dict[str, Any]],
    list[str],
]:
    """
    찾아보기 용어 목록을 기준으로 본문을 용어별로 분리합니다.
    """
    parsed_terms: list[dict[str, Any]] = []

    current_term: str | None = None
    current_description: list[str] = []
    current_related_terms: list[str] = []

    start_page: int | None = None
    end_page: int | None = None

    found_term_keys: set[str] = set()

    def save_current_term() -> None:
        nonlocal current_term
        nonlocal current_description
        nonlocal current_related_terms
        nonlocal start_page
        nonlocal end_page

        if current_term is None:
            return

        description = join_description(
            current_description
        )

        parsed_terms.append(
            {
                "term": current_term,
                "description": description,
                "related_terms": current_related_terms,
                "source_page_start": start_page,
                "source_page_end": end_page,
            }
        )

        current_term = None
        current_description = []
        current_related_terms = []
        start_page = None
        end_page = None

    for item in lines:
        page_number = item["page"]
        line = item["text"]

        line_key = normalize_term_key(line)

        if line_key in term_lookup:
            save_current_term()

            current_term = term_lookup[line_key]
            current_description = []
            current_related_terms = []
            start_page = page_number
            end_page = page_number

            found_term_keys.add(line_key)
            continue

        if current_term is None:
            continue

        if line.startswith("연관검색어"):
            current_related_terms = split_related_terms(
                line
            )
            end_page = page_number
            continue

        current_description.append(line)
        end_page = page_number

    save_current_term()

    missing_terms = [
        term
        for key, term in term_lookup.items()
        if key not in found_term_keys
    ]

    return parsed_terms, missing_terms


def validate_and_filter_terms(
    terms: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """설명 누락 등 기본 품질을 검사합니다."""
    valid_terms: list[dict[str, Any]] = []
    invalid_terms: list[dict[str, Any]] = []

    for term in terms:
        reasons: list[str] = []

        if not term["term"].strip():
            reasons.append("용어명이 비어 있음")

        if len(term["description"]) < 20:
            reasons.append("설명이 지나치게 짧음")

        if term["source_page_start"] is None:
            reasons.append("시작 페이지가 없음")

        if reasons:
            invalid_terms.append(
                {
                    **term,
                    "reasons": reasons,
                }
            )
        else:
            valid_terms.append(term)

    return valid_terms, invalid_terms


def remove_duplicate_terms(
    terms: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """같은 용어가 중복 추출된 경우 제거합니다."""
    unique_terms: list[dict[str, Any]] = []
    duplicate_terms: list[dict[str, Any]] = []

    seen: set[str] = set()

    for term in terms:
        key = normalize_term_key(term["term"])

        if key in seen:
            duplicate_terms.append(term)
            continue

        seen.add(key)
        unique_terms.append(term)

    return unique_terms, duplicate_terms


def assign_ids(
    terms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """용어마다 재사용 가능한 고유 ID를 부여합니다."""
    result: list[dict[str, Any]] = []

    for index, term in enumerate(
        terms,
        start=1,
    ):
        result.append(
            {
                "term_id": f"bok_term_{index:04d}",
                **term,
                "source": "한국은행 경제금융용어 800선",
                "source_year": 2026,
            }
        )

    return result


def save_json(
    data: Any,
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


def print_samples(
    terms: list[dict[str, Any]],
) -> None:
    print("\n[추출 샘플]")

    for term in terms[:5]:
        print("-" * 60)
        print(f"ID: {term['term_id']}")
        print(f"용어: {term['term']}")
        print(
            "페이지:",
            f"{term['source_page_start']}"
            f"~{term['source_page_end']}",
        )
        print(
            "연관검색어:",
            ", ".join(term["related_terms"]),
        )
        print(
            "설명:",
            term["description"][:150],
        )


def main() -> None:
    try:
        pages = load_pages(INPUT_PATH)

        index_terms = extract_index_terms(pages)

        print(
            f"[정보] 찾아보기에서 추출한 용어 수: "
            f"{len(index_terms)}"
        )

        term_lookup = build_term_lookup(
            index_terms
        )

        body_lines = clean_body_lines(
            pages,
            term_lookup,
        )

        print(
            f"[정보] 본문 정리 줄 수: "
            f"{len(body_lines):,}"
        )

        parsed_terms, missing_terms = parse_terms(
            lines=body_lines,
            term_lookup=term_lookup,
        )

        valid_terms, invalid_terms = (
            validate_and_filter_terms(
                parsed_terms
            )
        )

        unique_terms, duplicate_terms = (
            remove_duplicate_terms(
                valid_terms
            )
        )

        final_terms = assign_ids(
            unique_terms
        )

        output_data = {
            "metadata": {
                "dataset_name": (
                    "한국은행 경제금융용어 800선"
                ),
                "source_year": 2026,
                "record_count": len(final_terms),
                "index_term_count": len(index_terms),
                "body_page_range": {
                    "start": BODY_START_PAGE,
                    "end": BODY_END_PAGE,
                },
            },
            "terms": final_terms,
        }

        report_data = {
            "summary": {
                "index_term_count": len(index_terms),
                "parsed_term_count": len(parsed_terms),
                "valid_term_count": len(valid_terms),
                "final_term_count": len(final_terms),
                "expected_term_count": 800,
                "count_difference": (
                    len(final_terms) - 800
                ),
            },
            "missing_term_count": len(missing_terms),
            "missing_terms": missing_terms,
            "invalid_term_count": len(invalid_terms),
            "invalid_terms": invalid_terms,
            "duplicate_term_count": len(
                duplicate_terms
            ),
            "duplicate_terms": duplicate_terms,
        }

        save_json(
            output_data,
            OUTPUT_PATH,
        )

        save_json(
            report_data,
            REPORT_PATH,
        )

        print("\n[2차 파싱 결과]")
        print(
            f"- 찾아보기 용어: {len(index_terms)}개"
        )
        print(
            f"- 본문에서 발견: {len(parsed_terms)}개"
        )
        print(
            f"- 품질검증 통과: {len(valid_terms)}개"
        )
        print(
            f"- 중복 제거 후: {len(final_terms)}개"
        )
        print(
            f"- 찾지 못한 용어: {len(missing_terms)}개"
        )
        print(
            f"- 예상 800개와 차이: "
            f"{len(final_terms) - 800:+d}개"
        )
        print(f"- 결과 파일: {OUTPUT_PATH}")
        print(f"- 보고서: {REPORT_PATH}")

        print_samples(final_terms)

        if len(final_terms) == 800:
            print(
                "\n[완료] 800개 금융용어가 "
                "정상적으로 구조화되었습니다."
            )
        else:
            print(
                "\n[확인 필요] 보고서의 missing_terms와 "
                "invalid_terms를 확인해야 합니다."
            )

    except FileNotFoundError as error:
        print(f"[오류] {error}")
        raise SystemExit(1) from error

    except json.JSONDecodeError as error:
        print(f"[오류] JSON 형식 오류: {error}")
        raise SystemExit(1) from error

    except Exception as error:
        print(f"[오류] 2차 파싱 실패: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
