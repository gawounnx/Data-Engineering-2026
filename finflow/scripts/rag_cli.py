"""FinFlow CLI"""

from __future__ import annotations

import argparse

from rag import ask_finflow


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FinFlow 금융용어 RAG"
    )

    parser.add_argument(
        "--query",
        required=True,
        help="질문",
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="검색 개수",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    result = ask_finflow(
        query=args.query,
        top_k=args.top_k,
    )

    print("=" * 70)
    print("질문")
    print(result["query"])

    print("\n답변")
    print(result["answer"])

    print("\n참고 금융용어")

    for source in result["sources"]:
        print(
            f"- {source['term']} "
            f"(유사도 {source['similarity']:.4f})"
        )


if __name__ == "__main__":
    main()

