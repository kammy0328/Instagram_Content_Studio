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
    left, top, right, bottom = refine_margins(info, index, samples, (left, top, right, bottom), cancel)
    if not any((left, top, right, bottom)):
        return None
    return left, top, info.display_w - left - right, info.display_h - top - bottom


EDGE_LEVEL = 32  # 띠 경계로 볼 밝기 (압축 잡음/링잉으로 띠가 16을 조금 넘어도 영상으로 오인하지 않게)
STEP_LEVEL = 40  # 경계로 인정할 행 사이 밝기 증가량
EDGE_WINDOW = 8  # 축소 샘플 경계 오차를 보정하는 범위(원본 픽셀)


def edge_offsets(light: np.ndarray, margin: int, window: int = EDGE_WINDOW, level: int = EDGE_LEVEL) -> int:
    """한쪽 띠 경계를 원본 해상도에서 다듬는다. light: 띠 쪽 가장자리부터 안쪽 방향으로 정렬한 행(또는 열)별 밝기 분위수.
    margin 근처에서 처음으로 밝아지는 행을 영상의 시작으로 본다."""
    lo = max(1, margin - window)
    hi = min(len(light) - 1, margin + window)
    if hi >= lo:
        steps = np.diff(light[lo - 1 : hi + 1].astype(float))  # steps[i] = light[lo+i] - light[lo+i-1]
        i = int(np.argmax(steps))
        if steps[i] >= STEP_LEVEL:
            return lo + i  # 압축 링잉으로 띠가 흐려져도 가장 크게 밝아지는 지점이 영상의 시작이다
    for r in range(max(0, margin - window), hi + 1):
        if light[r] > level:
            return r
    return margin


def refine_margins(
    info: MediaInfo,
    index: FrameIndex,
    samples: list[int],
    margins: tuple[int, int, int, int],
    cancel: Event | None = None,
) -> tuple[int, int, int, int]:
    """축소(최대 960px) 감지 결과를 원본 해상도 프레임 몇 장으로 다듬는다. 압축 잡음 때문에 경계가 1~수 픽셀 안쪽/바깥쪽으로
    어긋나 검정 선이 남거나 영상이 깎이는 것을 줄인다. 프레임마다 가장 덜 자르는 값을 쓰고, 짝수 정렬은 띠를 남기지 않도록 올림한다."""
    left, top, right, bottom = margins
    picks = samples[:: max(1, len(samples) // 5)][:5]
    best = [10**9] * 4
    used = 0
    for frame in picks:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("검정 띠 감지를 취소했습니다.")
        a = decode_frame_rgb(info, index, frame)[0]
        light = a.max(axis=2)
        if np.quantile(light, 0.99) <= 32:
            continue  # 암전 프레임은 경계 판단에 쓰지 않는다
        used += 1
        rows = np.quantile(light, 0.995, axis=1)
        cols = np.quantile(light, 0.995, axis=0)
        cur = (
            edge_offsets(cols, left) if left else 0,
            edge_offsets(rows, top) if top else 0,
            edge_offsets(cols[::-1], right) if right else 0,
            edge_offsets(rows[::-1], bottom) if bottom else 0,
        )
        best = [min(b, c) for b, c in zip(best, cur)]
    if not used:
        return margins
    out = tuple(0 if m == 0 else min(m + EDGE_WINDOW, (b + 1) // 2 * 2) for m, b in zip(margins, best))
    return out  # type: ignore[return-value]
