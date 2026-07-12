import json, math, argparse
from sentence_transformers import SentenceTransformer

def cosine_similarity(a, b):
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x**2 for x in a))
    nb = math.sqrt(sum(x**2 for x in b))
    return dot/(na*nb) if na and nb else 0.0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--embeddings", default="data/embeddings.json")
    parser.add_argument("--model", default="kekeappa/kor-static-embedding-128")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    with open(args.embeddings) as f:
        e = json.load(f)
    records = e["records"]

    model = SentenceTransformer(args.model)
    vec = model.encode([args.query])[0].tolist()
    scored = sorted(records, key=lambda r: cosine_similarity(vec, r["embedding"]), reverse=True)[:args.top_k]

    print(f"\n쿼리: {args.query}\n")
    for i, r in enumerate(scored, 1):
        m = r["metadata"]
        score = cosine_similarity(vec, r["embedding"])
        print(f"[{i}] chunk_id: {r['chunk_id']}")
        print(f"     score   : {score:.4f}")
        print(f"     text    : {r['text'][:300]}...")
        print(f"     {m['age']}세 {m['sex']} / {m['occupation']} / {m['district']}")
        print()

if __name__ == "__main__":
    main()
