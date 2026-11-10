"""Small-corpus BM25 and reciprocal-rank fusion, independent of model providers."""
import math
import re
from collections import Counter


def tokenize(text):
    tokens = []
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.match(r"[a-z0-9_]", part) or len(part) == 1:
            tokens.append(part)
        else:
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
    return tokens


def bm25(query, candidates, limit=30):
    terms = set(tokenize(query))
    docs = [Counter(tokenize(c["title"] + "\n" + c["text"])) for c in candidates]
    n = len(docs)
    avg = sum(sum(d.values()) for d in docs) / max(n, 1) or 1
    df = Counter(t for d in docs for t in d)
    ranked = []
    for c, counts in zip(candidates, docs, strict=True):
        score = 0.0
        for t in terms & counts.keys():
            frequency = counts[t]
            idf = math.log(1 + (n - df[t] + .5) / (df[t] + .5))
            score += idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * sum(counts.values()) / avg))
        if score > 0:
            ranked.append({**c, "score": score})
    return sorted(ranked, key=lambda c: (-c["score"], c["id"]))[:limit]


def fuse(rankings, limit=30):
    scores, hits = {}, {}
    for name, ranking in rankings:
        for rank, candidate in enumerate(ranking, 1):
            cid = candidate["id"]
            scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
            hits.setdefault(cid, {**candidate, "channels": {}})["channels"][name] = {
                "rank": rank, "score": candidate["score"]}
    return [{**hits[cid], "score": scores[cid]} for cid in
            sorted(scores, key=lambda cid: (-scores[cid], cid))[:limit]]
