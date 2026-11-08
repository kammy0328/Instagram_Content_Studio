"""여러 원본 프레임의 공통 검정 띠 감지. 샘플 사이의 모든 장면을 보장하지는 않는다."""

from __future__ import annotations

from collections.abc import Iterable
from threading import Event

import numpy as np

from .decode import decode_frame_rgb
from .frameindex import FrameIndex
from .media import MediaInfo


def detect_bounds(frames: Iterable[np.ndarray]) -> tuple[int, int, int, int] | None:
    """RGB 샘플에서 확인된 공통 영역. 페이드/암전은 제외하고, 반대편 띠가 없는 어두운 구도는 유지한다."""
    margins = None
    shape = None
    for a in frames:
        h, w = a.shape[:2]
        if shape is not None and shape != (h, w):
            raise ValueError("감지 프레임의 크기가 서로 다릅니다.")
        shape = h, w
        light = a.max(axis=2)
        if np.quantile(light, 0.99) <= 32:
            continue  # 암전만으로 영상 영역을 추측하지 않는다
        rows = np.quantile(light, 0.995, axis=1) <= 16
        cols = np.quantile(light, 0.995, axis=0) <= 16

        def edges(mask):
            visible = np.flatnonzero(~mask)
            if not len(visible):
                return 0, 0
            lo, hi = int(visible[0]), len(mask) - 1 - int(visible[-1])
            if min(lo, hi) < 2 or max(lo, hi) > len(mask) * 0.35 or max(lo, hi) > 2 * min(lo, hi):
                return 0, 0
            return lo, hi

        top, bottom = edges(rows)
        left, right = edges(cols)
        current = (left, top, right, bottom)
        margins = current if margins is None else tuple(min(a, b) for a, b in zip(margins, current))
    if margins is None or not any(margins):
        return None
    left, top, right, bottom = margins
    h, w = shape
    return left, top, w - left - right, h - top - bottom


def detect_letterbox(
    info: MediaInfo,
    index: FrameIndex,
    start: int,
    end: int,
    cancel: Event | None = None,
) -> tuple[int, int, int, int] | None:
    """선택 구간에서 최대 15개 프레임을 원본 디코딩. 정사각 표시 좌표로 반환한다 (SAR/회전 반영)."""
    if not (0 <= start < end <= index.count):
        raise ValueError("검정 띠를 감지할 구간이 올바르지 않습니다.")
    k = min(1, 960 / info.display_w, 960 / info.display_h)
    w, h = max(2, int(info.display_w * k)), max(2, int(info.display_h * k))
    n = min(15, end - start)
    samples = sorted({start + (end - start - 1) * i // max(1, n - 1) for i in range(n)})

    def frames():
        for frame in samples:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("검정 띠 감지를 취소했습니다.")
            yield decode_frame_rgb(info, index, frame, w, h)[0]

    crop = detect_bounds(frames())
    if not crop:
        return None
    x, y, cw, ch = crop
    # 축소 샘플의 경계 오차는 영상 안쪽 대신 띠 쪽으로 반올림한다. 색차 서브샘플링에 맞게 짝수 정렬.
    left = int(x * info.display_w / w) // 2 * 2
    top = int(y * info.display_h / h) // 2 * 2
    right = int((w - x - cw) * info.display_w / w) // 2 * 2
    bottom = int((h - y - ch) * info.display_h / h) // 2 * 2
    if not any((left, top, right, bottom)):
        return None
    return left, top, info.display_w - left - right, info.display_h - top - bottom
