"""Bounded energy-onset candidates on the project's rational frame clock."""

from fractions import Fraction

import numpy as np
from pydantic import Field

from app.timeline_models import TimelineModel


class BeatAnalysis(TimelineModel):
    frames: list[int] = Field(default_factory=list, max_length=10000)
    bpm: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


def detect_beats(samples, sample_rate: int, fps_num: int, fps_den: int) -> BeatAnalysis:
    samples = np.asarray(samples)
    if (
        not 8000 <= sample_rate <= 48000
        or not 0 < fps_num / fps_den <= 120
        or samples.ndim != 1
        or samples.size > sample_rate * 1800
        or not np.isfinite(samples).all()
    ):
        raise ValueError("Use finite mono samples spanning at most30minutes")
    if not samples.size:
        return BeatAnalysis()
    hop = max(1, sample_rate // 200)
    values = samples.astype(np.float32, copy=False)
    if np.issubdtype(samples.dtype, np.integer):
        values = values / np.iinfo(samples.dtype).max
    values = np.pad(values, (0, (-values.size) % hop))
    energy = np.mean(values.reshape(-1, hop) ** 2, axis=1)
    if float(energy.max()) < 1e-8:
        return BeatAnalysis()
    # Positive energy change locates onsets, including an onset at the range origin.
    novelty = np.maximum(0, np.diff(energy, prepend=0))
    median = float(np.median(novelty))
    threshold = max(1e-8, float(novelty.max()) * 0.08, median * 4)
    peaks = np.flatnonzero(
        (novelty > threshold) & (novelty >= np.roll(novelty, 1)) & (novelty >= np.roll(novelty, -1))
    )
    accepted = []
    minimum_gap = sample_rate * 0.12 / hop
    for index in peaks:
        if not accepted or index - accepted[-1] >= minimum_gap:
            accepted.append(int(index))
        elif novelty[index] > novelty[accepted[-1]]:
            accepted[-1] = int(index)
    if len(accepted) > 10000:
        raise ValueError("Too many onset candidates. Select a shorter range")
    rate = Fraction(fps_num, fps_den)
    frames = sorted({round(Fraction(index * hop, sample_rate) * rate) for index in accepted})
    bpm, confidence = None, None
    if len(accepted) >= 3:
        intervals = np.diff(accepted) * hop / sample_rate
        period = float(np.median(intervals))
        estimate = 60 / period
        if 20 <= estimate <= 300:
            bpm = round(estimate, 3)
            confidence = float(np.mean(np.abs(intervals - period) <= period * 0.15))
    return BeatAnalysis(frames=frames, bpm=bpm, confidence=confidence)
