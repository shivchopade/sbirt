"""SBERT embedder: model loaded once, batch encoding, L2-normalised vectors (cosine = dot product), LRU cache."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Sequence

import numpy as np

log = logging.getLogger(__name__)


class Embedder:
    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 64, cache_size: int = 8192):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.batch_size = batch_size
        self.cache_size = cache_size
        try:
            self.model = SentenceTransformer(model_name, device=device)
            self.device = device
        except Exception:  # pragma: no cover - e.g. MPS not usable inside a VM
            log.warning("could not load SBERT on %s, falling back to CPU", device)
            self.model = SentenceTransformer(model_name, device="cpu")
            self.device = "cpu"
        getter = (
            getattr(self.model, "get_embedding_dimension", None)
            or self.model.get_sentence_embedding_dimension
        )
        self.dim = int(getter())
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        if not texts:
            return out
        with self._lock:
            missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
            if missing:
                vectors = self.model.encode(
                    list(missing),
                    batch_size=self.batch_size,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
                for text, vec in zip(missing, vectors, strict=True):
                    self._cache[text] = vec.astype(np.float32)
                    if len(self._cache) > self.cache_size:
                        self._cache.popitem(last=False)
            for i, text in enumerate(texts):
                vec = self._cache.get(text)
                if vec is None:  # evicted while encoding a very large batch
                    vec = self.model.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
                else:
                    self._cache.move_to_end(text)
                out[i] = vec
        return out


def mean_vector(vectors: np.ndarray) -> np.ndarray | None:
    if vectors.size == 0:
        return None
    mean = vectors.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm > 0 else None
