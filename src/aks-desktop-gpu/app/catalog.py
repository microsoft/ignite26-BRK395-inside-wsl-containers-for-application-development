"""Embedding models competing in the arena via FastEmbed / ONNX Runtime CUDA."""

QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

MODELS = [
    {"id": "BAAI/bge-small-en-v1.5", "name": "BGE Small v1.5", "org": "BAAI", "dim": 384, "params": "33M", "query_prefix": QUERY_INSTRUCTION},
    {"id": "sentence-transformers/all-MiniLM-L6-v2", "name": "all-MiniLM-L6-v2", "org": "SBERT", "dim": 384, "params": "22M"},
    {"id": "snowflake/snowflake-arctic-embed-xs", "name": "Arctic Embed XS", "org": "Snowflake", "dim": 384, "params": "22M", "query_prefix": QUERY_INSTRUCTION},
    {"id": "jinaai/jina-embeddings-v2-small-en", "name": "Jina v2 Small", "org": "Jina AI", "dim": 512, "params": "33M"},
    {"id": "snowflake/snowflake-arctic-embed-s", "name": "Arctic Embed S", "org": "Snowflake", "dim": 384, "params": "33M", "query_prefix": QUERY_INSTRUCTION},
    {"id": "BAAI/bge-base-en-v1.5", "name": "BGE Base v1.5", "org": "BAAI", "dim": 768, "params": "110M", "query_prefix": QUERY_INSTRUCTION},
    {"id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "name": "Multilingual MiniLM L12", "org": "SBERT", "dim": 384, "params": "118M"},
    {"id": "thenlper/gte-base", "name": "GTE Base", "org": "Alibaba", "dim": 768, "params": "110M"},
    {"id": "snowflake/snowflake-arctic-embed-m", "name": "Arctic Embed M", "org": "Snowflake", "dim": 768, "params": "110M", "query_prefix": QUERY_INSTRUCTION},
    {"id": "nomic-ai/nomic-embed-text-v1.5", "name": "Nomic Embed v1.5", "org": "Nomic", "dim": 768, "params": "137M", "query_prefix": "search_query: ", "doc_prefix": "search_document: "},
    {"id": "jinaai/jina-embeddings-v2-base-en", "name": "Jina v2 Base", "org": "Jina AI", "dim": 768, "params": "137M"},
    {"id": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2", "name": "Multilingual MPNet Base", "org": "SBERT", "dim": 768, "params": "278M"},
    {"id": "BAAI/bge-large-en-v1.5", "name": "BGE Large v1.5", "org": "BAAI", "dim": 1024, "params": "335M", "query_prefix": QUERY_INSTRUCTION},
    {"id": "mixedbread-ai/mxbai-embed-large-v1", "name": "mxbai Embed Large", "org": "Mixedbread", "dim": 1024, "params": "335M", "query_prefix": QUERY_INSTRUCTION},
]


def slug(model_id: str) -> str:
    s = model_id.split("/")[-1].lower()
    return "".join(c if c.isalnum() else "-" for c in s).strip("-")[:40]


BY_ID = {m["id"]: m for m in MODELS}
