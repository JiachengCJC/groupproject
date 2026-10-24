"""Versioned semantic indexing in Qdrant, using its documented REST API.

PostgreSQL/SQLite remains authoritative. Qdrant stores vectors and identifiers;
search always filters the complete authorized chunk set and verifies hashes again.
"""
from hashlib import sha256
import json
import math
import uuid

import httpx

from .providers import Providers, ProviderError


def content_hash(chunk):
    return sha256(json.dumps({k: chunk.get(k) for k in ("id", "content", "title", "location")},
                             ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def scope_id(db):
    return str(getattr(db, "workspace_id", None) or "default")


def point_id(scope, chunk_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "market-lens:"+scope+":"+chunk_id))


class VectorIndex:
    def __init__(self, db, settings, providers=None):
        self.db, self.settings = db, settings
        self.providers = providers or Providers(settings)
        self.url = (getattr(settings, "qdrant_url", "") or "").rstrip("/")
        if not self.url:
            raise ProviderError("Semantic retrieval requires QDRANT_URL. Select lexical retrieval explicitly for no-model mode.")
        self.providers.require_local_endpoint(self.url)
        self.scope = scope_id(db)
        self.provider = getattr(settings, "embedding_provider", "lexical")
        self.model = getattr(settings, "embedding_model", "")
        self.providers.assert_mode(self.provider, self.model)
        if self.provider not in {"ollama", "cloud", "live"} or not self.model:
            raise ProviderError("Configure a local or cloud semantic embedding model before indexing.")
        base_url = settings.ollama_base_url if self.provider == "ollama" else getattr(settings, "cloud_base_url", "")
        self.fingerprint = ["chunk-v2", self.provider, self.model, base_url]
        self.revision = None
        self.collection = "market_lens_" + sha256(json.dumps(self.fingerprint).encode()).hexdigest()[:20]
        key = getattr(settings, "qdrant_api_key", "")
        self.headers = {"api-key": key} if key else {}

    def request(self, method, path, payload=None, *, missing_ok=False):
        try:
            response = httpx.request(method, self.url+path, json=payload, headers=self.headers,
                                    timeout=self.providers.network_timeout(30), follow_redirects=False, trust_env=False)
            if missing_ok and response.status_code == 404:
                return None
            response.raise_for_status()
            result = response.json()
            if result.get("status") not in ("ok", None):
                raise ValueError("Qdrant rejected the operation")
            return result.get("result")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProviderError(f"Vector index request failed ({type(exc).__name__}). Check Qdrant health and connection settings.") from exc

    def info(self):
        return self.request("GET", "/collections/"+self.collection, missing_ok=True)

    def index(self, chunks):
        if not chunks:
            return {"collection": self.collection, "chunk_count": 0, "indexed": 0, "reused": 0}
        if self.revision is None:
            self.revision = self.providers.embedding_revision()
            self.collection = "market_lens_" + sha256(json.dumps([*self.fingerprint, self.revision]).encode()).hexdigest()[:20]
        info = self.info()
        existing = {}
        if info is not None:
            for start in range(0, len(chunks), 128):
                ids = [point_id(self.scope, c["id"]) for c in chunks[start:start+128]]
                points = self.request("POST", "/collections/"+self.collection+"/points",
                                      {"ids": ids, "with_payload": True, "with_vector": False})
                if not isinstance(points, list) or any(not isinstance(p, dict) or "id" not in p for p in points):
                    raise ProviderError("Qdrant returned an invalid stored-points response.")
                existing.update({p["id"]: p.get("payload", {}) for p in points})
        pending = [c for c in chunks if existing.get(point_id(self.scope, c["id"]), {}).get("content_hash") != content_hash(c)]
        for start in range(0, len(pending), 32):
            batch = pending[start:start+32]
            vectors = self.providers.embed([c["title"]+"\n"+c["content"] for c in batch])
            if info is None:
                # A concurrent indexer may create the same versioned collection.
                try:
                    self.request("PUT", "/collections/"+self.collection,
                                 {"vectors": {"size": len(vectors[0]), "distance": "Cosine"}})
                except ProviderError:
                    if self.info() is None:
                        raise
                info = self.info()
            if not isinstance(info, dict):
                raise ProviderError("Qdrant returned an invalid collection configuration.")
            dimensions = info.get("config", {}).get("params", {}).get("vectors", {}).get("size")
            if dimensions != len(vectors[0]):
                raise ProviderError("Embedding dimensions changed. Use a versioned embedding model name and rebuild its index.")
            points = [{"id": point_id(self.scope, c["id"]), "vector": vector,
                       "payload": {"workspace_id": self.scope, "chunk_id": c["id"],
                                   "document_id": c["document_id"], "content_hash": content_hash(c)}}
                      for c, vector in zip(batch, vectors)]
            self.request("PUT", "/collections/"+self.collection+"/points?wait=true", {"points": points})
        return {"collection": self.collection, "chunk_count": len(chunks), "indexed": len(pending),
                "reused": len(chunks)-len(pending), "embedding_provider": self.provider, "embedding_model": self.model,
                "embedding_revision": self.revision}

    def search(self, query, chunks, limit=12):
        if not chunks:
            return []
        self.index(chunks)
        vectors = self.providers.embed([query])
        allowed = {point_id(self.scope, c["id"]): c for c in chunks}
        # Split the allow-list to keep Qdrant request sizes bounded. A global top-k
        # is recovered by merging the per-shard top-k scores.
        ranked = []
        ids = list(allowed)
        for start in range(0, len(ids), 1024):
            result = self.request("POST", "/collections/"+self.collection+"/points/query", {
                "query": vectors[0], "filter": {"must": [
                    {"key": "workspace_id", "match": {"value": self.scope}},
                    {"has_id": ids[start:start+1024]},
                ]}, "limit": min(limit, 100), "with_payload": True, "with_vector": False,
            })
            if not isinstance(result, dict) or not isinstance(result.get("points"), list):
                raise ProviderError("Qdrant returned an invalid search response.")
            for hit in result["points"]:
                if not isinstance(hit, dict) or "id" not in hit:
                    raise ProviderError("Qdrant returned an invalid search hit.")
                chunk = allowed.get(str(hit["id"]))
                payload = hit.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                score = hit.get("score")
                if (chunk is not None and payload.get("workspace_id") == self.scope
                        and payload.get("content_hash") == content_hash(chunk)
                        and isinstance(score, (float, int)) and not isinstance(score, bool) and math.isfinite(score)):
                    ranked.append({**chunk, "semantic_score": float(score), "source_hash": content_hash(chunk)})
        return sorted(ranked, key=lambda c: (-c["semantic_score"], c["id"]))[:limit]


def index_documents(db, settings, dataset_id=None, document_ids=None, providers=None):
    """Index an authorized dataset/doc selection; no selection indexes this workspace."""
    if dataset_id:
        db.dataset(dataset_id)
    if not dataset_id and not document_ids:
        document_ids = [d["id"] for d in db.documents()]
    chunks = db.chunks(dataset_id, document_ids)
    result = VectorIndex(db, settings, providers).index(chunks)
    return {**result, "document_count": len({c["document_id"] for c in chunks}), "mode": "semantic"}
