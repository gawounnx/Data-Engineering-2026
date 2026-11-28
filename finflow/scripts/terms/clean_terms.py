"""[3/7] 금융용어 PDF 추출 과정에서 발생한 텍스트 오류를 보수적으로 정제한다.

입력: data/terms/processed/financial_terms_800_v3.json
출력: financial_terms_800_v4_cleaned.json, financial_terms_cleaning_report_v4.json
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import terms_config


INPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_800_v3.json"
)

OUTPUT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_800_v4_cleaned.json"
)

REPORT_PATH = (
    terms_config.processed_dir()
    / "financial_terms_cleaning_report_v4.json"
)


# 확실하게 잘못된 것으로 확인된 문자열만 교체한다.
FIXED_REPLACEMENTS: dict[str, str] = {
    "원리금상환비 율": "원리금상환비율",
    "현 물이전": "현물이전",
    "고위험가 구": "고위험가구",
    "많 ㅅ 다": "많다",
    "1 ㅅ 분위": "1분위",
    "비 용항목": "비용항목",
    "자 산": "자산",
    "현 금흐름": "현금흐름",
    "통 화선물": "통화선물",
    "통 화스왑": "통화스왑",
    "통 화옵션": "통화옵션",
    "환율변 동": "환율변동",
    "가격경 쟁력": "가격경쟁력",
    "연 금기금": "연금기금",
    "연구개 발": "연구개발",
    "소유권이 전": "소유권이전",
    "분 류될": "분류될",
    "의 해": "의해",
    "긴 축": "긴축",
    "주택가 격": "주택가격",
    "연 상시킨다": "연상시킨다",
    "활성화하 려": "활성화하려",
    "그 린": "그린",
    "구하 는데": "구하는데",
    "의 미한다": "의미한다",
    "충 격": "충격",
    "이 를": "이를",
    "가능성 이": "가능성이",
    "정책 의": "정책의",
    "능력 이": "능력이",
    "환 율": "환율",
    "의 미 한다": "의미한다",
    "1 을": "1을",
    "활성화하 려는": "활성화하려는",
    "기업가 에게": "기업가에게",
    "가운 데": "가운데",
    "혁신기업 의": "혁신기업의",
    "안전 장치": "안전장치",
    "발행기관 의": "발행기관의",
    "기 업홍보": "기업홍보",
    "기 회": "기회",
    "운 영": "운영",
    "있 다": "있다",
    "것 으로": "것으로",
    "본 다": "본다",
    "6 개": "6개",
    "금융상 황": "금융상황",
        # 기념화폐
    "특 ᄀ 별히": "특별히",
    "기 념적": "기념적",
    "기 념은행권": "기념은행권",

    # 동아시아 외환위기
    "급락 한 데": "급락한 데",
    "국가 들은": "국가들은",
    "외환위기 의": "외환위기의",

    # 뮤추얼펀드
    "하나이 다": "하나이다",
    "주 요": "주요",

    # 발행중지화폐 / 유통정지화폐
    "중지하기 로": "중지하기로",
    "강제통용력 도": "강제통용력도",
    "금융기 관": "금융기관",
    "유통정지화폐 는": "유통정지화폐는",

    # 추가 OCR 띄어쓰기 오류
    "전반적으 로": "전반적으로",
    "구 입": "구입",
    "작 성한": "작성한",
    "작 성하는": "작성하는",
    "차 이": "차이",


}


DEFINITE_FIXES: list[tuple[str, str, str]] = [
    (
        r"(?<!\d)(\d{4})\s+년(?!\d)",
        r"\1년",
        "연도: N년",
    ),
    (
        r"(?<!\d)(\d{1,2})\s+월(?!\d)",
        r"\1월",
        "월: N월",
    ),
]


# 문자열에 이 형태가 있으면 완전히 정제되지 않았을 가능성이 있다.
SUSPICIOUS_PATTERNS: dict[str, re.Pattern[str]] = {
    "isolated_korean_jamo": re.compile(
        r"(?<![ㄱ-ㅎㅏ-ㅣ])[ㄱ-ㅎㅏ-ㅣ](?![ㄱ-ㅎㅏ-ㅣ])"
    ),
    "multiple_spaces": re.compile(r" {2,}"),
    "space_before_punctuation": re.compile(
        r"\s+[,.;:!?%]"
    ),
    "broken_number_unit": re.compile(
        r"\d+\s+[가-힣]\s+[가-힣]+"
    ),
}


def load_dataset(
    input_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """금융용어 JSON을 불러온다."""
    if not input_path.exists():
        raise FileNotFoundError(
            f"입력 파일을 찾을 수 없습니다: {input_path}"
        )

    with input_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    metadata = payload.get("metadata")
    terms = payload.get("terms")

    if not isinstance(metadata, dict):
        raise ValueError(
            "metadata가 딕셔너리 형식이 아닙니다."
        )

    if not isinstance(terms, list):
        raise ValueError(
            "terms가 리스트 형식이 아닙니다."
        )

    if not terms:
        raise ValueError(
            "정제할 금융용어 데이터가 없습니다."
        )

    return metadata, terms


def normalize_basic_text(text: str) -> str:
    """유니코드, 줄바꿈, 공백을 기본 정규화한다."""
    normalized = unicodedata.normalize(
        "NFKC",
        text,
    )

    normalized = normalized.replace(
        "\u00a0",
        " ",
    )

    normalized = normalized.replace(
        "\r\n",
        "\n",
    ).replace(
        "\r",
        "\n",
    )

    normalized = re.sub(
        r"[ \t]+",
        " ",
        normalized,
    )

    normalized = re.sub(
        r" *\n *",
        "\n",
        normalized,
    )

    normalized = re.sub(
        r"\n{3,}",
        "\n\n",
        normalized,
    )

    normalized = re.sub(
        r"\s+([,.;:!?%])",
        r"\1",
        normalized,
    )

    normalized = re.sub(
        r"([\(\[\{])\s+",
        r"\1",
        normalized,
    )

    normalized = re.sub(
        r"\s+([\)\]\}])",
        r"\1",
        normalized,
    )

    return normalized.strip()


def apply_fixed_replacements(
    text: str,
    replacement_counts: Counter[str],
) -> str:
    """확실하게 잘못된 문자열을 지정된 규칙으로 교체한다."""
    cleaned = text

    for wrong_text, correct_text in FIXED_REPLACEMENTS.items():
        count = cleaned.count(wrong_text)

        if count > 0:
            cleaned = cleaned.replace(
                wrong_text,
                correct_text,
            )

            replacement_counts[
                f"{wrong_text} -> {correct_text}"
            ] += count

    return cleaned


def clean_text(
    text: Any,
    replacement_counts: Counter[str],
) -> str:
    """문자열을 보수적으로 정제한다."""
    if text is None:
        return ""

    cleaned = normalize_basic_text(
        str(text)
    )

    # 정규식 기반 확정 교정
    for pattern, replacement, label in DEFINITE_FIXES:
        new_cleaned = re.sub(pattern, replacement, cleaned)

        if new_cleaned != cleaned:
            replacement_counts[label] += 1

        cleaned = new_cleaned

    # 문자열 치환
    cleaned = apply_fixed_replacements(
        cleaned,
        replacement_counts,
    )

    cleaned = normalize_basic_text(
        cleaned
    )

    return cleaned


def clean_related_terms(
    related_terms: Any,
    replacement_counts: Counter[str],
) -> list[str]:
    """연관 용어 목록을 정제한다."""
    if not isinstance(related_terms, list):
        return []

    cleaned_terms: list[str] = []

    for related_term in related_terms:
        cleaned = clean_text(
            related_term,
            replacement_counts,
        )

        if cleaned:
            cleaned_terms.append(cleaned)

    return list(dict.fromkeys(cleaned_terms))


def find_suspicious_patterns(
    text: str,
) -> list[str]:
    """아직 남아 있는 의심스러운 패턴을 찾는다."""
    found_patterns: list[str] = []

    for pattern_name, pattern in SUSPICIOUS_PATTERNS.items():
        if pattern.search(text):
            found_patterns.append(
                pattern_name
            )

    return found_patterns


def clean_term_record(
    record: dict[str, Any],
    replacement_counts: Counter[str],
) -> tuple[dict[str, Any], list[str]]:
    """금융용어 한 건을 정제한다."""
    cleaned_record = dict(record)

    original_term = str(
        record.get("term", "")
    )

    original_description = str(
        record.get("description", "")
    )

    cleaned_term = clean_text(
        original_term,
        replacement_counts,
    )

    cleaned_description = clean_text(
        original_description,
        replacement_counts,
    )

    cleaned_record["term"] = cleaned_term
    cleaned_record["description"] = (
        cleaned_description
    )

    cleaned_record["related_terms"] = (
        clean_related_terms(
            record.get("related_terms", []),
            replacement_counts,
        )
    )

    suspicious_patterns = (
        find_suspicious_patterns(
            cleaned_description
        )
    )

    changed_fields: list[str] = []

    if original_term != cleaned_term:
        changed_fields.append("term")

    if original_description != cleaned_description:
        changed_fields.append("description")

    return cleaned_record, [
        *changed_fields,
        *suspicious_patterns,
    ]


def validate_cleaned_terms(
    terms: list[dict[str, Any]],
) -> None:
    """정제된 데이터의 필수값과 중복을 검증한다."""
    term_ids: list[str] = []
    term_names: list[str] = []

    for index, record in enumerate(terms):
        term_id = str(
            record.get("term_id", "")
        ).strip()

        term = str(
            record.get("term", "")
        ).strip()

        description = str(
            record.get("description", "")
        ).strip()

        if not term_id:
            raise ValueError(
                f"{index}번째 레코드의 term_id가 비어 있습니다."
            )

        if not term:
            raise ValueError(
                f"{term_id}의 term이 비어 있습니다."
            )

        if not description:
            raise ValueError(
                f"{term_id}의 description이 비어 있습니다."
            )

        term_ids.append(term_id)
        term_names.append(term)

    if len(term_ids) != len(set(term_ids)):
        raise ValueError(
            "중복된 term_id가 있습니다."
        )

    duplicated_terms = [
        term
        for term, count in Counter(
            term_names
        ).items()
        if count > 1
    ]

    if duplicated_terms:
        print(
            "[경고] 중복 용어명이 있습니다:",
            duplicated_terms[:10],
        )


def save_cleaned_dataset(
    original_metadata: dict[str, Any],
    cleaned_terms: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """정제된 금융용어 데이터를 저장한다."""
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_metadata = {
        **original_metadata,
        "dataset_name": (
            "financial_terms_800_v4_cleaned"
        ),
        "record_count": len(cleaned_terms),
        "preprocessing_version": "v4",
        "preprocessing_method": (
            "unicode_normalization_and_conservative_ocr_replacements"
        ),
        "source_file": terms_config.display_path(INPUT_PATH),
    }

    payload = {
        "metadata": output_metadata,
        "terms": cleaned_terms,
    }

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )


def save_cleaning_report(
    report: dict[str, Any],
    report_path: Path,
) -> None:
    """텍스트 정제 보고서를 저장한다."""
    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with report_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            ensure_ascii=False,
            indent=2,
        )


def main() -> None:
    """금융용어 텍스트 정제를 실행한다."""
    try:
        metadata, terms = load_dataset(
            INPUT_PATH
        )

        replacement_counts: Counter[str] = (
            Counter()
        )

        cleaned_terms: list[dict[str, Any]] = []
        suspicious_records: list[dict[str, Any]] = []
        changed_record_count = 0

        for record in terms:
            cleaned_record, findings = (
                clean_term_record(
                    record=record,
                    replacement_counts=(
                        replacement_counts
                    ),
                )
            )

            cleaned_terms.append(
                cleaned_record
            )

            changed_fields = [
                item
                for item in findings
                if item in {
                    "term",
                    "description",
                }
            ]

            suspicious_patterns = [
                item
                for item in findings
                if item not in {
                    "term",
                    "description",
                }
            ]

            if changed_fields:
                changed_record_count += 1

            if suspicious_patterns:
                suspicious_records.append(
                    {
                        "term_id": cleaned_record[
                            "term_id"
                        ],
                        "term": cleaned_record[
                            "term"
                        ],
                        "patterns": (
                            suspicious_patterns
                        ),
                        "description_preview": (
                            cleaned_record[
                                "description"
                            ][:250]
                        ),
                    }
                )

        validate_cleaned_terms(
            cleaned_terms
        )

        save_cleaned_dataset(
            original_metadata=metadata,
            cleaned_terms=cleaned_terms,
            output_path=OUTPUT_PATH,
        )

        report = {
            "input_file": terms_config.display_path(INPUT_PATH),
            "output_file": terms_config.display_path(OUTPUT_PATH),
            "original_record_count": len(terms),
            "cleaned_record_count": len(
                cleaned_terms
            ),
            "changed_record_count": (
                changed_record_count
            ),
            "replacement_counts": dict(
                replacement_counts
            ),
            "replacement_total": sum(
                replacement_counts.values()
            ),
            "suspicious_record_count": len(
                suspicious_records
            ),
            "suspicious_records": (
                suspicious_records
            ),
        }

        save_cleaning_report(
            report=report,
            report_path=REPORT_PATH,
        )

        print("[완료] 금융용어 텍스트 정제 성공")
        print(
            f"- 원본 레코드: {len(terms):,}건"
        )
        print(
            f"- 정제 레코드: {len(cleaned_terms):,}건"
        )
        print(
            f"- 변경된 레코드: "
            f"{changed_record_count:,}건"
        )
        print(
            f"- 명시적 문자열 교체: "
            f"{sum(replacement_counts.values()):,}회"
        )
        print(
            f"- 추가 확인 필요: "
            f"{len(suspicious_records):,}건"
        )
        print(
            f"- 결과 파일: {OUTPUT_PATH.resolve()}"
        )
        print(
            f"- 정제 보고서: {REPORT_PATH.resolve()}"
        )

        if replacement_counts:
            print("\n[주요 교체 내역]")

            for replacement, count in (
                replacement_counts.most_common(
                    10
                )
            ):
                print(
                    f"- {replacement}: {count}회"
                )

        if suspicious_records:
            print("\n[추가 확인 샘플]")

            for item in suspicious_records[:5]:
                print("-" * 60)
                print(
                    f"term_id: {item['term_id']}"
                )
                print(
                    f"term: {item['term']}"
                )
                print(
                    f"patterns: {item['patterns']}"
                )
                print(
                    "preview:",
                    item["description_preview"],
                )

    except FileNotFoundError as exc:
        print(f"[파일 오류] {exc}")
        raise SystemExit(1) from exc

    except ValueError as exc:
        print(f"[검증 오류] {exc}")
        raise SystemExit(1) from exc

    except KeyboardInterrupt:
        print(
            "\n[중단] 사용자가 정제를 중단했습니다."
        )
        raise SystemExit(130)

    except Exception as exc:
        print(f"[예상하지 못한 오류] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
