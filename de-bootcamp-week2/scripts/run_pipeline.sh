#!/usr/bin/env bash
set -euo pipefail

LIMIT="${DOWNLOAD_LIMIT:-50}"

python scripts/day0_download_personas.py --limit "${LIMIT}" --output data/personas_raw.json
python scripts/day1_preprocess_chunk.py --input data/personas_raw.json --output data/chunks.json
python scripts/day2_local_embedding.py --input data/chunks.json --output data/embeddings.json

echo "[완료] 2주차 파이프라인: personas -> chunks -> embeddings"
