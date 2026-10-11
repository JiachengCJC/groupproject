"""Local sparse TF-IDF vectors, cosine retrieval, and inspectable source chunks."""
from collections import Counter
import math
import re

STOP = set("a an and are as at be by for from has have how i in is it of on or our should that the their this to we what which with would".split())


def tokens(text):
    words = [t for t in re.findall(r"[^\W_]+", text.lower(), re.UNICODE) if t not in STOP and len(t) > 1]
    # CJK character bigrams work without whitespace or a downloaded embedding model.
    for run in re.findall(r"[\u3400-\u9fff\u3040-\u30ff]+", text):
        words.extend(run[i:i+2] for i in range(len(run)-1))
    return words


def retrieve(db, query, dataset_id=None, limit=3, document_ids=None, fallback=False):
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
    result = sorted(ranked, key=lambda x: x["relevance"], reverse=True)[:limit]
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
