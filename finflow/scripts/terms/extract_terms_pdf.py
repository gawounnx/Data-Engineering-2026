"""[1/7] 한국은행 「2026 경제금융용어 800선」 PDF에서 페이지별 텍스트를 추출한다.

입력: data/terms/raw/2026_경제금융용어 800선.pdf (TERMS_PDF_PATH로 변경 가능)
출력: data/terms/processed/financial_terms_pages.json, financial_terms_raw.txt
외부 호출·운영 데이터 쓰기 없음.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pdfplumber

import terms_config


PDF_PATH = terms_config.pdf_path()

OUTPUT_DIR = terms_config.processed_dir()
RAW_TEXT_PATH = OUTPUT_DIR / "financial_terms_raw.txt"
PAGES_JSON_PATH = OUTPUT_DIR / "financial_terms_pages.json"


def extract_pages(pdf_path: Path) -> list[dict[str, Any]]:
    """PDF 전체 페이지의 텍스트를 페이지별로 추출합니다."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

    pages_data: list[dict[str, Any]] = []

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        print(f"[정보] 전체 페이지 수: {total_pages}")

        for page_index, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
                cleaned_text = text.strip()

                pages_data.append(
                    {
                        "page": page_index,
                        "text": cleaned_text,
                        "character_count": len(cleaned_text),
                    }
                )

                if page_index % 20 == 0 or page_index == total_pages:
                    print(
                        f"[진행] {page_index}/{total_pages}페이지 추출 완료"
                    )

            except Exception as error:
                print(f"[경고] {page_index}페이지 추출 실패: {error}")

                pages_data.append(
                    {
                        "page": page_index,
                        "text": "",
                        "character_count": 0,
                        "error": str(error),
                    }
                )

    return pages_data


def save_raw_text(
    pages_data: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """페이지별 텍스트를 하나의 TXT 파일로 저장합니다."""
    sections: list[str] = []

    for page_data in pages_data:
        page_number = page_data["page"]
        text = page_data["text"]

        section = (
            f"===== PDF {page_number}페이지 =====\n"
            f"{text}\n"
        )
        sections.append(section)

    output_path.write_text(
        "\n".join(sections),
        encoding="utf-8",
    )


def save_pages_json(
    pages_data: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """페이지별 추출 결과를 JSON 파일로 저장합니다."""
    output_data = {
        "source": PDF_PATH.name,
        "total_pages": len(pages_data),
        "pages_with_text": sum(
            1 for page in pages_data if page["text"]
        ),
        "pages": pages_data,
    }

    output_path.write_text(
        json.dumps(
            output_data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def print_summary(pages_data: list[dict[str, Any]]) -> None:
    """추출 결과 요약을 출력합니다."""
    total_pages = len(pages_data)
    pages_with_text = sum(
        1 for page in pages_data if page["text"]
    )
    empty_pages = total_pages - pages_with_text
    total_characters = sum(
        page["character_count"] for page in pages_data
    )

    print("\n[추출 결과]")
    print(f"- 전체 페이지: {total_pages}")
    print(f"- 텍스트 추출 페이지: {pages_with_text}")
    print(f"- 빈 페이지: {empty_pages}")
    print(f"- 전체 문자 수: {total_characters:,}")
    print(f"- 전체 텍스트: {RAW_TEXT_PATH}")
    print(f"- 페이지별 JSON: {PAGES_JSON_PATH}")


def main() -> None:
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        pages_data = extract_pages(PDF_PATH)

        save_raw_text(
            pages_data=pages_data,
            output_path=RAW_TEXT_PATH,
        )

        save_pages_json(
            pages_data=pages_data,
            output_path=PAGES_JSON_PATH,
        )

        print_summary(pages_data)

    except FileNotFoundError as error:
        print(f"[오류] {error}")
        raise SystemExit(1) from error

    except Exception as error:
        print(f"[오류] PDF 전체 추출 중 문제가 발생했습니다: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
