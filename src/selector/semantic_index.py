"""
Semantic product index (embedding model + pluggable vector store).

Builds a persistent vector index over the product catalog so AI-mode selection
can recall products by **meaning**, not just the keyword/spec SQL filters the
rule matcher uses. Embeddings come from the configured ``llm.embedding`` task
(``bge-m3`` on local Ollama by default).

The vector-store backend is pluggable via ``selector.vector_backend``:

- ``embedded`` (default) — a pure-Python numpy cosine store persisted to disk.
  Zero native dependencies, so it works where chromadb's prebuilt Windows wheel
  segfaults on ``add()``. At the catalog's scale (hundreds of products, 1024-dim
  vectors) brute-force cosine is sub-millisecond — the ANN index a full vector
  DB provides buys nothing here.
- ``chroma`` — a Chroma ``PersistentClient`` collection. Use when the catalog
  grows large enough to need ANN, on a platform where Chroma's native wheel
  works (Linux, or Windows via a Docker/WSL Chroma server).

Both backends store the same shape (id = product model, the embedded document,
filterable metadata) and return the same ``SemanticHit`` (cosine distance,
lower = closer), so AIMatcher integration is backend-agnostic.

Design notes
------------
- The index is a *derivative cache* of the SQLite catalog. It is always safe to
  delete and rebuild (``python -m src.cli index build``). Rebuild after each
  crawl so newly-added products become searchable.
- We compute embeddings ourselves (via the router) and hand the store raw
  vectors for both ``add`` and ``query`` — documents and queries are guaranteed
  to share the same model; mixing models would make distances meaningless.
- Reads are best-effort: ``query`` returns ``[]`` on an empty/missing index or
  any search error, so AIMatcher can degrade to the rule shortlist. ``build``
  raises so the CLI can report a real error (Ollama down, model not pulled, …).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig, get_config
from ..llm import get_router
from ..storage import get_db
from ..storage.models import Product

logger = logging.getLogger(__name__)

# Embedded document length cap. bge-m3 handles long context, but product specs
# are short; capping keeps batches fast and avoids pathological brochure dumps.
_MAX_DOC_CHARS = 2000


def product_document(p: Product) -> str:
    """Compose the text embedded for a product.

    Front-load the human-meaningful fields (name, category, description) since
    those carry the strongest semantic signal, then append compact spec tokens
    so a query like "万兆三层 国产 核心交换机" lands near the right rows.
    """
    parts: list[str] = []
    if p.name:
        parts.append(p.name)
    if p.model:
        parts.append(p.model)
    if p.series:
        parts.append(f"系列:{p.series}")
    cat = "/".join(x for x in (p.category, p.sub_category) if x)
    if cat:
        parts.append(f"类别:{cat}")
    if p.section:
        parts.append("创新产品/自主可控" if p.section == "innovation" else "通用产品")
    if p.description:
        parts.append(p.description)

    specs: list[str] = []
    if p.port_count and p.port_speed:
        specs.append(f"{p.port_count}×{p.port_speed}端口")
    elif p.port_speed:
        specs.append(f"{p.port_speed}端口")
    if p.layer:
        specs.append(str(p.layer))
    if p.switching_capacity_gbps:
        specs.append(f"交换容量{p.switching_capacity_gbps}Gbps")
    if p.poe:
        specs.append("PoE供电")
    if p.redundant_power:
        specs.append("冗余电源")
    if p.is_domestic:
        specs.append("国产化")
    if specs:
        parts.append(" ".join(specs))

    # A few free-form specs the parser stashed in extra_specs (capped so a
    # giant brochure table can't dominate the embedding).
    if isinstance(p.extra_specs, dict):
        for k, v in list(p.extra_specs.items())[:8]:
            if isinstance(v, (str, int, float)) and str(v).strip():
                parts.append(f"{k}:{v}")

    return " | ".join(parts)[:_MAX_DOC_CHARS]


@dataclass
class SemanticHit:
    """One vector-search result. `distance` is cosine distance (lower = closer)."""

    model: str
    distance: float
    metadata: dict


def _metadata(p: Product) -> dict:
    """Filterable metadata stored alongside each vector.

    Values are restricted to str/int/float/bool (never None): Chroma requires
    it, and the embedded store keeps the same shape for parity, so empty strings
    stand in for a missing section/category.
    """
    return {
        "model": p.model or "",
        "section": p.section or "",
        "category": p.category or "",
        "is_domestic": bool(p.is_domestic) if p.is_domestic is not None else False,
    }


# ---------------------------------------------------------------------------
# Vector-store backends
#
# Each backend is a thin storage adapter. Shared logic (embedding via the
# router, document composition, batching, model-whitelist post-filtering) lives
# in SemanticIndex; backends only persist vectors and answer nearest-neighbour
# searches. Contract:
#   count() -> int
#   reset() -> None                      drop everything (start a fresh build)
#   add(ids, embeddings, documents, metadatas) -> None
#   search(qvec, n, section) -> list[SemanticHit]   closest-first, section-filtered
# ---------------------------------------------------------------------------
class _EmbeddedStore:
    """Pure-Python vector store: brute-force cosine over a persisted matrix.

    Persisted as two sibling files under ``index_dir``:
      ``embedded_index.npz``  — float32 matrix [N, dim], rows L2-normalized
      ``embedded_index.json`` — parallel ids / documents / metadatas
    Rows are stored normalized so search is a single matrix-vector product.
    """

    def __init__(self, index_dir: Path):
        self._dir = Path(index_dir)
        self._loaded = False
        self._matrix = None             # np.ndarray [N, dim] float32 (normalized)
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._metas: list[dict] = []

    def _npz_path(self) -> Path:
        return self._dir / "embedded_index.npz"

    def _meta_path(self) -> Path:
        return self._dir / "embedded_index.json"

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        import numpy as np

        self._loaded = True
        npz, meta = self._npz_path(), self._meta_path()
        if not (npz.exists() and meta.exists()):
            self._matrix, self._ids, self._docs, self._metas = None, [], [], []
            return
        try:
            with np.load(npz) as data:
                self._matrix = np.array(data["matrix"], dtype=np.float32)
            obj = json.loads(meta.read_text(encoding="utf-8"))
            self._ids = list(obj.get("ids", []))
            self._docs = list(obj.get("documents", []))
            self._metas = list(obj.get("metadatas", []))
            if self._matrix.shape[0] != len(self._ids):
                raise ValueError("matrix/ids length mismatch")
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Embedded index load failed (%s); treating as empty.", exc)
            self._matrix, self._ids, self._docs, self._metas = None, [], [], []

    def count(self) -> int:
        self._ensure_loaded()
        return 0 if self._matrix is None else int(self._matrix.shape[0])

    def reset(self) -> None:
        self._matrix, self._ids, self._docs, self._metas = None, [], [], []
        self._loaded = True
        for p in (self._npz_path(), self._meta_path()):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except Exception as exc:                        # noqa: BLE001
                logger.warning("Embedded index reset: could not delete %s (%s)", p, exc)

    def add(self, ids, embeddings, documents, metadatas) -> None:
        import numpy as np

        self._ensure_loaded()
        mat = np.asarray(embeddings, dtype=np.float32)
        if mat.ndim != 2:
            mat = mat.reshape(len(ids), -1)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = (mat / norms).astype(np.float32)
        if self._matrix is None or self._matrix.shape[0] == 0:
            self._matrix = mat
        else:
            self._matrix = np.vstack([self._matrix, mat])
        self._ids += list(ids)
        self._docs += list(documents)
        self._metas += list(metadatas)
        self._persist()

    def _persist(self) -> None:
        import numpy as np

        self._dir.mkdir(parents=True, exist_ok=True)
        np.savez(self._npz_path(), matrix=self._matrix)
        self._meta_path().write_text(
            json.dumps(
                {"ids": self._ids, "documents": self._docs, "metadatas": self._metas},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def search(self, qvec, n: int, section: str | None) -> list[SemanticHit]:
        import numpy as np

        self._ensure_loaded()
        if self._matrix is None or self._matrix.shape[0] == 0:
            return []
        q = np.asarray(qvec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn
        sims = self._matrix @ q                              # cosine similarity

        if section:
            cand = [i for i, m in enumerate(self._metas) if (m or {}).get("section") == section]
            if not cand:
                return []
            cand_arr = np.asarray(cand, dtype=np.int64)
            order = cand_arr[np.argsort(-sims[cand_arr])]
        else:
            order = np.argsort(-sims)

        hits: list[SemanticHit] = []
        for idx in order[: max(n, 0)]:
            i = int(idx)
            hits.append(
                SemanticHit(
                    model=self._ids[i],
                    distance=1.0 - float(sims[i]),
                    metadata=self._metas[i] or {},
                )
            )
        return hits


class _ChromaStore:
    """Chroma PersistentClient backend (kept for large catalogs / Linux/Docker)."""

    def __init__(self, index_dir: Path, collection: str):
        self._dir = str(index_dir)
        self._collection = collection
        self._client = None
        self._coll_obj = None

    def _client_(self):
        if self._client is None:
            import chromadb  # local import: keep chromadb off the no-LLM path

            self._client = chromadb.PersistentClient(path=self._dir)
        return self._client

    def _coll(self):
        if self._coll_obj is None:
            self._coll_obj = self._client_().get_or_create_collection(
                name=self._collection, metadata={"hnsw:space": "cosine"}
            )
        return self._coll_obj

    def count(self) -> int:
        try:
            return self._coll().count()
        except Exception:                                   # noqa: BLE001
            return 0

    def reset(self) -> None:
        client = self._client_()
        try:
            client.delete_collection(self._collection)
        except Exception:                                   # noqa: BLE001
            pass  # didn't exist — fine
        self._coll_obj = client.get_or_create_collection(
            name=self._collection, metadata={"hnsw:space": "cosine"}
        )

    def add(self, ids, embeddings, documents, metadatas) -> None:
        self._coll().add(
            ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas
        )

    def search(self, qvec, n: int, section: str | None) -> list[SemanticHit]:
        where = {"section": section} if section else None
        res = self._coll().query(query_embeddings=[qvec], n_results=n, where=where)
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        return [
            SemanticHit(model=mid, distance=float(dist), metadata=meta or {})
            for mid, dist, meta in zip(ids, dists, metas)
        ]


def _make_backend(cfg: AppConfig):
    """Pick the vector-store backend from config (default: embedded)."""
    backend = (getattr(cfg.selector, "vector_backend", "embedded") or "embedded").strip().lower()
    index_dir = Path(cfg.storage.chroma_dir)
    if backend == "chroma":
        return _ChromaStore(index_dir, cfg.storage.chroma_collection)
    if backend not in ("embedded", "numpy"):
        logger.warning("Unknown selector.vector_backend %r; using 'embedded'.", backend)
    return _EmbeddedStore(index_dir)


class SemanticIndex:
    """Persistent semantic index over the product catalog (backend-agnostic)."""

    def __init__(self, cfg: AppConfig | None = None):
        self._cfg = cfg or get_config()
        self._backend = _make_backend(self._cfg)

    def count(self) -> int:
        """Number of indexed products (0 when the index doesn't exist yet)."""
        return self._backend.count()

    # ---- build --------------------------------------------------------------
    def build(self, products: list[Product] | None = None, *, batch_size: int = 64) -> int:
        """(Re)build the index from scratch and return the number of vectors.

        Resets the store first so deletions/renames in the catalog don't leave
        stale rows behind. Embeddings are fetched in batches via the router,
        then handed to the backend as raw vectors.
        """
        db = get_db()
        items = list(products if products is not None else db.all_products())
        items = [p for p in items if (p.model or "").strip()]

        # Reset unconditionally so a stale index never lingers after the catalog
        # shrinks to zero or a rebuild starts.
        self._backend.reset()
        if not items:
            logger.warning("SemanticIndex.build: no products to index.")
            return 0

        router = get_router()
        n = 0
        for i in range(0, len(items), batch_size):
            chunk = items[i : i + batch_size]
            docs = [product_document(p) for p in chunk]
            vecs = router.embed("embedding", docs)
            self._backend.add(
                ids=[p.model for p in chunk],
                embeddings=vecs,
                documents=docs,
                metadatas=[_metadata(p) for p in chunk],
            )
            n += len(chunk)
            logger.info("SemanticIndex.build: embedded %d/%d", n, len(items))
        return n

    # ---- query --------------------------------------------------------------
    def query(
        self,
        text: str,
        *,
        n: int = 20,
        section: str | None = None,
        allowed_models: list[str] | set[str] | None = None,
    ) -> list[SemanticHit]:
        """Vector-search the index for products semantically close to `text`.

        section        — restrict to "innovation" | "general" via metadata filter.
        allowed_models — post-filter to this model whitelist (used for 名录 scope);
                         we over-fetch then trim so the filter doesn't starve `n`.

        Returns [] (never raises) when the index is empty, the query text is
        blank, or any embedding/search error occurs, so callers can treat
        semantic recall as best-effort.
        """
        text = (text or "").strip()
        if not text:
            return []
        total = self._backend.count()
        if total == 0:
            return []

        try:
            qvec = get_router().embed("embedding", [text])[0]
        except Exception as exc:                            # noqa: BLE001
            logger.warning("SemanticIndex.query: embedding failed (%s)", exc)
            return []

        allowed = set(allowed_models) if allowed_models is not None else None
        # Over-fetch when we'll post-filter by model whitelist.
        want = n if allowed is None else max(n * 3, n + 20)
        want = min(want, total)

        try:
            hits = self._backend.search(qvec, want, section)
        except Exception as exc:                            # noqa: BLE001
            logger.warning("SemanticIndex.query: search failed (%s)", exc)
            return []

        if allowed is not None:
            hits = [h for h in hits if h.model in allowed]
        return hits[:n]


__all__ = ["SemanticIndex", "SemanticHit", "product_document"]
