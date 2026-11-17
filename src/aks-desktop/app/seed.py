"""Seeds Postgres/pgvector with a retrieval benchmark (BEIR SciFact subset)."""
import json
import os
import random
import sys
import time
import urllib.request

from common import Reporter, connect

CACHE = os.environ.get("CACHE_DIR", "/cache") + "/datasets/scifact"
CORPUS_SIZE = int(os.environ.get("CORPUS_SIZE", "500"))
MAX_CHARS = 600
BASE = "https://huggingface.co/datasets/mteb/scifact/resolve/main/"
FILES = {"corpus.jsonl": BASE + "corpus.jsonl", "queries.jsonl": BASE + "queries.jsonl", "test.tsv": BASE + "qrels/test.tsv"}

rep = Reporter("__seed__")


def download():
    os.makedirs(CACHE, exist_ok=True)
    for i, (name, url) in enumerate(FILES.items()):
        path = os.path.join(CACHE, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        rep.send(force=True, phase="downloading", progress=i / len(FILES), message=f"fetching {name}")
        tmp = path + ".part"
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if total:
                    rep.send(phase="downloading", progress=(i + got / total) / len(FILES), message=f"fetching {name}")
        os.replace(tmp, path)


def main():
    t0 = time.time()
    rep.send(force=True, phase="connecting", progress=0, message="connecting to postgres")
    conn = connect("arena-seeder")
    download()

    rep.send(force=True, phase="preparing", progress=0, message="sampling corpus")
    qrels = {}
    with open(os.path.join(CACHE, "test.tsv")) as f:
        next(f)
        for line in f:
            qid, did, score = line.strip().split("\t")
            if int(score) > 0:
                qrels.setdefault(qid, set()).add(did)
    queries = {}
    with open(os.path.join(CACHE, "queries.jsonl")) as f:
        for line in f:
            q = json.loads(line)
            if q["_id"] in qrels:
                queries[q["_id"]] = q["text"]
    corpus = {}
    with open(os.path.join(CACHE, "corpus.jsonl")) as f:
        for line in f:
            d = json.loads(line)
            corpus[d["_id"]] = (d.get("title") or "", d.get("text") or "")

    relevant = sorted({d for ds in qrels.values() for d in ds if d in corpus})
    others = sorted(set(corpus) - set(relevant))
    random.Random(42).shuffle(others)
    chosen = relevant + others[: max(0, CORPUS_SIZE - len(relevant))]

    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("DROP TABLE IF EXISTS qrels, queries, docs CASCADE")
        cur.execute("CREATE TABLE docs (id serial PRIMARY KEY, ext_id text UNIQUE, title text, body text)")
        cur.execute("CREATE TABLE queries (id serial PRIMARY KEY, ext_id text UNIQUE, body text)")
        cur.execute("CREATE TABLE qrels (query_id int REFERENCES queries(id), doc_id int REFERENCES docs(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS results (model_id text PRIMARY KEY, metrics jsonb, finished_at timestamptz DEFAULT now())")
        cur.execute("TRUNCATE results")

        total = len(chosen) + len(queries)
        done = 0
        for start in range(0, len(chosen), 100):
            batch = chosen[start : start + 100]
            cur.executemany(
                "INSERT INTO docs (ext_id, title, body) VALUES (%s, %s, %s)",
                [(d, corpus[d][0], (corpus[d][0] + ". " + corpus[d][1])[:MAX_CHARS]) for d in batch],
            )
            done += len(batch)
            rep.send(phase="loading", progress=done / total, message=f"inserted {done}/{len(chosen)} documents")
        cur.executemany("INSERT INTO queries (ext_id, body) VALUES (%s, %s)", list(queries.items()))
        cur.execute(
            "INSERT INTO qrels (query_id, doc_id) SELECT q.id, d.id FROM queries q, docs d, "
            "unnest(%s::text[], %s::text[]) AS r(qid, did) WHERE q.ext_id = r.qid AND d.ext_id = r.did",
            ([q for q, ds in qrels.items() for _ in ds], [d for ds in qrels.values() for d in ds]),
        )
        cur.execute("ANALYZE")
        cur.execute("SELECT (SELECT count(*) FROM docs), (SELECT count(*) FROM queries), (SELECT count(*) FROM qrels)")
        n_docs, n_q, n_rel = cur.fetchone()

    rep.send(
        force=True, phase="completed", progress=1,
        message=f"{n_docs} docs, {n_q} queries, {n_rel} judgements",
        dataset={"name": "BEIR SciFact", "docs": n_docs, "queries": n_q, "qrels": n_rel, "seconds": round(time.time() - t0, 1)},
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        rep.send(force=True, phase="failed", message=str(exc)[:300])
        raise
    sys.exit(0)
