"""Colour maths: WCAG contrast and background estimation from rendered pixels."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RGB = tuple[int, int, int]


def _channel(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: RGB) -> float:
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: RGB, b: RGB) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg: RGB, bg: RGB, alpha: float) -> RGB:
    a = max(0.0, min(1.0, alpha))
    return tuple(round(a * f + (1 - a) * b) for f, b in zip(fg, bg, strict=True))  # type: ignore[return-value]


def distance(a: RGB, b: RGB) -> int:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


@dataclass
class PixelStats:
    mode_rgb: RGB  # most frequent colour in the crop
    mode_share: float
    second_rgb: RGB | None  # most frequent colour clearly different from the mode
    second_share: float
    ink_fraction: float  # share of pixels that differ clearly from the mode colour
    text_like_fraction: float  # share of pixels close to the (effective) text colour
    width: int
    height: int


def crop(render: np.ndarray, bbox: tuple[float, float, float, float], dpi: int) -> np.ndarray | None:
    scale = dpi / 72.0
    h, w = render.shape[:2]
    x0, y0 = max(0, int(bbox[0] * scale)), max(0, int(bbox[1] * scale))
    x1, y1 = min(w, int(np.ceil(bbox[2] * scale))), min(h, int(np.ceil(bbox[3] * scale)))
    if x1 <= x0 or y1 <= y0:
        return None
    return render[y0:y1, x0:x1]


def pixel_stats(region: np.ndarray, text_rgb: RGB, ink_delta: int) -> PixelStats:
    pixels = region.reshape(-1, 3).astype(np.int16)
    quant = (pixels // 16).astype(np.int32)
    keys = quant[:, 0] * 256 + quant[:, 1] * 16 + quant[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    total = len(keys)
    mode_mask = keys == uniq[order[0]]
    mode_rgb: RGB = tuple(int(v) for v in pixels[mode_mask].mean(axis=0).round())  # type: ignore[assignment]
    second_rgb: RGB | None = None
    second_share = 0.0
    for idx in order[1:]:
        mask = keys == uniq[idx]
        cand: RGB = tuple(int(v) for v in pixels[mask].mean(axis=0).round())  # type: ignore[assignment]
        if distance(cand, mode_rgb) > ink_delta:
            second_rgb, second_share = cand, counts[idx] / total
            break
    diff = np.abs(pixels - np.array(mode_rgb, dtype=np.int16)).max(axis=1)
    ink_fraction = float((diff > ink_delta).mean())
    tdiff = np.abs(pixels - np.array(text_rgb, dtype=np.int16)).max(axis=1)
    text_like = float((tdiff <= ink_delta).mean())
    return PixelStats(
        mode_rgb=mode_rgb,
        mode_share=float(counts[order[0]] / total),
        second_rgb=second_rgb,
        second_share=float(second_share),
        ink_fraction=ink_fraction,
        text_like_fraction=text_like,
        width=region.shape[1],
        height=region.shape[0],
    )


def estimate_background(stats: PixelStats, text_rgb: RGB, ink_delta: int, ink_share: float) -> RGB:
    """Most frequent colour under the span — unless that colour *is* the glyph ink (heavy icon glyphs)."""
    if (
        distance(stats.mode_rgb, text_rgb) <= ink_delta
        and stats.second_rgb is not None
        and stats.second_share >= ink_share
    ):
        return stats.second_rgb
    return stats.mode_rgb
