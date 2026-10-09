"""Torch device auto-selection: CUDA → Apple MPS (native macOS only) → CPU."""

from __future__ import annotations


def select_device(preference: str = "auto") -> str:
    if preference != "auto":
        return preference
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"
