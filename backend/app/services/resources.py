"""Heavy, shared, read-only resources (SBERT model, spaCy pipeline, skill catalogue) — loaded once per process."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

from app.core.config import Settings
from app.core.device import select_device
from app.services.preprocessing.lexicons import injection_phrases
from app.services.preprocessing.nlp import NlpEngine
from app.services.scoring.embedder import Embedder
from app.services.scoring.skills import SkillCatalog

log = logging.getLogger(__name__)


@dataclass
class Resources:
    settings: Settings
    embedder: Embedder
    nlp: NlpEngine
    catalog: SkillCatalog
    device: str
    injection_vectors: np.ndarray


def load_resources(settings: Settings, *, check_dim: bool = True) -> Resources:
    """check_dim=False is for offline scripts that compare models without a database."""
    started = time.perf_counter()
    device = select_device(settings.device)
    embedder = Embedder(settings.sbert_model, device, settings.embed_batch_size, settings.embed_cache_size)
    if check_dim and embedder.dim != settings.embedding_dim:
        raise RuntimeError(
            f"{settings.sbert_model} produces {embedder.dim}-dim vectors but EMBEDDING_DIM={settings.embedding_dim}; "
            "set EMBEDDING_DIM to match and run a migration"
        )
    catalog = SkillCatalog()
    nlp = NlpEngine(settings.spacy_model, catalog)
    resources = Resources(
        settings=settings,
        embedder=embedder,
        nlp=nlp,
        catalog=catalog,
        device=embedder.device,
        injection_vectors=embedder.encode(list(injection_phrases())),
    )
    log.info(
        "models loaded",
        extra={
            "sbert": settings.sbert_model,
            "device": embedder.device,
            "seconds": round(time.perf_counter() - started, 2),
        },
    )
    return resources
