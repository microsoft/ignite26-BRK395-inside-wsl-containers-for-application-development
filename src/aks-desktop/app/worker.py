"""Benchmarks one embedding model against the pgvector database.

Phases: connecting -> downloading/loading model -> embedding corpus -> indexing -> querying -> completed
"""
import math
import os
import resource
import statistics
import sys
import time

import numpy as np

from catalog import BY_ID, slug
from common import Reporter, connect

MODEL_ID = os.environ["MODEL_ID"]
THREADS = int(os.environ.get("THREADS", "4"))
CACHE = os.environ.get("CACHE_DIR", "/cache") + "/fastembed"
BATCH = 16
K = 10

spec = BY_ID.get(MODEL_ID, {"id": MODEL_ID})
SLUG = slug(MODEL_ID)
TABLE = "emb_" + SLUG.replace("-", "_")
rep = Reporter(MODEL_ID)

# Overall progress budget per phase (fractions of the bar)
W_LOAD, W_EMBED, W_INDEX, W_QUERY = 0.10, 0.65, 0.05, 0.20


def rss_mb():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def main():
    t_start = time.time()
    rep.send(force=True, phase="connecting", progress=0, message="opening connection to pgvector")
    conn = connect(f"arena-{SLUG}"[:63])
    from pgvector.psycopg import register_vector

    register_vector(conn)

    rep.send(force=True, phase="loading", progress=0.01, message="downloading / loading ONNX weights")
    from fastembed import TextEmbedding

    t0 = time.time()
    model = TextEmbedding(model_name=MODEL_ID, cache_dir=CACHE, threads=THREADS)
    list(model.embed(["warm up"]))
    load_s = time.time() - t0
    rep.send(force=True, phase="loading", progress=W_LOAD, message=f"model ready in {load_s:.1f}s")

    with conn.cursor() as cur:
        cur.execute("SELECT id, body FROM docs ORDER BY id")
        docs = cur.fetchall()
        cur.execute("SELECT id, body FROM queries ORDER BY id")
        queries = cur.fetchall()
        cur.execute("SELECT query_id, doc_id FROM qrels")
        qrels = {}
        for q, d in cur.fetchall():
            qrels.setdefault(q, set()).add(d)

    doc_prefix = spec.get("doc_prefix", "")
    query_prefix = spec.get("query_prefix", "")

    # --- embed corpus and stream vectors into postgres ---
    embed_time = 0.0
    dim = None
    with conn.cursor() as cur:
        for i in range(0, len(docs), BATCH):
            batch = docs[i : i + BATCH]
            t0 = time.time()
            vecs = list(model.embed([doc_prefix + b for _, b in batch], batch_size=BATCH))
            embed_time += time.time() - t0
            if dim is None:
                dim = len(vecs[0])
                cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
                cur.execute(f"CREATE TABLE {TABLE} (id int PRIMARY KEY REFERENCES docs(id), embedding vector({dim}))")
            cur.executemany(f"INSERT INTO {TABLE} (id, embedding) VALUES (%s, %s)", [(d[0], np.asarray(v, dtype=np.float32)) for d, v in zip(batch, vecs)])
            done = i + len(batch)
            rep.send(
                phase="embedding", progress=W_LOAD + W_EMBED * done / len(docs),
                message=f"embedded {done}/{len(docs)} docs", live={"docs_per_sec": round(done / max(embed_time, 1e-6), 1), "rss_mb": rss_mb()},
            )
    docs_per_sec = len(docs) / max(embed_time, 1e-6)

    # --- build ANN index ---
    rep.send(force=True, phase="indexing", progress=W_LOAD + W_EMBED, message="building HNSW index (cosine)")
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute(f"CREATE INDEX ON {TABLE} USING hnsw (embedding vector_cosine_ops)")
        cur.execute(f"SELECT pg_total_relation_size('{TABLE}')")
        table_bytes = cur.fetchone()[0]
    index_s = time.time() - t0

    # --- retrieval evaluation ---
    base = W_LOAD + W_EMBED + W_INDEX
    ndcgs, recalls, mrrs, search_ms = [], [], [], []
    q_embed_time = 0.0
    with conn.cursor() as cur:
        cur.execute("SET hnsw.ef_search = 100")
        for i in range(0, len(queries), BATCH):
            batch = queries[i : i + BATCH]
            t0 = time.time()
            qvecs = list(model.embed([query_prefix + b for _, b in batch], batch_size=BATCH))
            q_embed_time += time.time() - t0
            for (qid, _), qv in zip(batch, qvecs):
                t0 = time.perf_counter()
                cur.execute(f"SELECT id FROM {TABLE} ORDER BY embedding <=> %s LIMIT {K}", (np.asarray(qv, dtype=np.float32),))
                hits = [r[0] for r in cur.fetchall()]
                search_ms.append((time.perf_counter() - t0) * 1000)
                rel = qrels.get(qid, set())
                dcg = sum(1 / math.log2(r + 2) for r, h in enumerate(hits) if h in rel)
                idcg = sum(1 / math.log2(r + 2) for r in range(min(len(rel), K)))
                ndcgs.append(dcg / idcg if idcg else 0)
                recalls.append(len(rel.intersection(hits)) / len(rel) if rel else 0)
                mrrs.append(next((1 / (r + 1) for r, h in enumerate(hits) if h in rel), 0))
            done = i + len(batch)
            rep.send(
                phase="querying", progress=base + W_QUERY * done / len(queries),
                message=f"searched {done}/{len(queries)} queries",
                live={"ndcg10": round(statistics.mean(ndcgs), 4), "rss_mb": rss_mb()},
            )

    metrics = {
        "ndcg10": round(statistics.mean(ndcgs), 4),
        "recall10": round(statistics.mean(recalls), 4),
        "mrr10": round(statistics.mean(mrrs), 4),
        "docs_per_sec": round(docs_per_sec, 1),
        "query_embed_ms": round(q_embed_time / len(queries) * 1000, 2),
        "search_ms_p50": round(statistics.median(search_ms), 2),
        "search_ms_p95": round(sorted(search_ms)[int(len(search_ms) * 0.95) - 1], 2),
        "load_s": round(load_s, 1),
        "index_s": round(index_s, 2),
        "total_s": round(time.time() - t_start, 1),
        "dim": dim,
        "rss_mb": rss_mb(),
        "table_mb": round(table_bytes / 1e6, 2),
        "docs": len(docs),
        "queries": len(queries),
        "threads": THREADS,
    }
    with conn.cursor() as cur:
        from psycopg.types.json import Jsonb

        cur.execute(
            "INSERT INTO results (model_id, metrics) VALUES (%s, %s) ON CONFLICT (model_id) DO UPDATE SET metrics = EXCLUDED.metrics, finished_at = now()",
            (MODEL_ID, Jsonb(metrics)),
        )
    rep.send(force=True, phase="completed", progress=1, message=f"nDCG@10 {metrics['ndcg10']:.3f}", metrics=metrics)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        rep.send(force=True, phase="failed", message=f"{type(exc).__name__}: {str(exc)[:300]}")
        raise
    sys.exit(0)
