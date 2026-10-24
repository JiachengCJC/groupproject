"""Local sparse TF-IDF vectors, cosine retrieval, and inspectable source chunks."""
from collections import Counter
import math
import re

STOP = set("a an and are as at be by for from has have how i in is it of on or our should that the their this to we what which with would".split())


def index_documents(db, settings, dataset_id=None, document_ids=None, providers=None):
    from .vector_store import index_documents as build_index
    return build_index(db, settings, dataset_id, document_ids, providers)


def tokens(text):
    words = [t for t in re.findall(r"[^\W_]+", text.lower(), re.UNICODE) if t not in STOP and len(t) > 1]
    # CJK character bigrams work without whitespace or a downloaded embedding model.
    for run in re.findall(r"[\u3400-\u9fff\u3040-\u30ff]+", text):
        words.extend(run[i:i+2] for i in range(len(run)-1))
    return words


def retrieve(db, query, dataset_id=None, limit=3, document_ids=None, fallback=False, *, settings=None, providers=None):
    chunks = db.chunks(dataset_id, document_ids)
    if not chunks:
        return []
    counts = [Counter(tokens(c["title"] + " " + c["content"])) for c in chunks]
    document_frequency = Counter(t for count in counts for t in count)
    idf = {t: math.log((1+len(chunks))/(1+n))+1 for t, n in document_frequency.items()}
    def vector(count):
        weighted = {t: (1+math.log(n))*idf[t] for t, n in count.items() if t in idf}
        norm = math.sqrt(sum(v*v for v in weighted.values())) or 1
        return {t: v/norm for t, v in weighted.items()}
    q = vector(Counter(tokens(query)))
    ranked = []
    for chunk, count in zip(chunks, counts):
        v = vector(count)
        score = sum(weight*v.get(t, 0) for t, weight in q.items())
        if score > 0:
            ranked.append({**chunk, "relevance": round(score, 4)})
    ranked = sorted(ranked, key=lambda x: (-x["relevance"], x["id"]))
    mode = getattr(settings, "retrieval_mode", "lexical") if settings else "lexical"
    if mode not in {"lexical", "hybrid", "semantic"}:
        raise ValueError("Unknown retrieval mode.")
    if mode != "lexical":
        from .vector_store import VectorIndex
        semantic = VectorIndex(db, settings, providers).search(query, chunks, limit=max(12, limit*3))
        if mode == "semantic":
            return [{**c, "relevance": round(c["semantic_score"], 4), "retrieval_method": "semantic"} for c in semantic[:limit]]
        # Reciprocal rank fusion combines incomparable lexical and cosine scores.
        # This is deterministic rank fusion, not a claimed learned reranker.
        fused = {}
        for method, candidates in (("lexical", ranked[:max(12, limit*3)]), ("semantic", semantic)):
            for rank, chunk in enumerate(candidates, 1):
                hit = fused.setdefault(chunk["id"], {**chunk, "fusion_score": 0, "retrieval_method": "hybrid_rrf"})
                hit[method+"_rank"] = rank
                hit["fusion_score"] += 1/(60+rank)
                if "semantic_score" in chunk:
                    hit["semantic_score"] = chunk["semantic_score"]
        result = sorted(fused.values(), key=lambda x: (-x["fusion_score"], x["id"]))[:limit]
        return [{**c, "relevance": round(c["fusion_score"], 6)} for c in result]
    result = ranked[:limit]
    if not result and fallback:
        # A broad summary has no exact keywords: include diverse selected documents.
        seen = set()
        for chunk in chunks:
            if chunk["document_id"] not in seen:
                result.append({**chunk, "relevance": 0, "retrieval_note": "Selected-document preview; no keyword match."})
                seen.add(chunk["document_id"])
            if len(result) >= limit:
                break
    return result
