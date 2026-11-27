"""릴스 커버 추천: 얼굴이 정면에 가깝고, 초점이 맞고(흐린 프레임 제외), 적당히 크게 나온 프레임을 고른다.

- 영상 전체에서 고르게 표본을 뽑는다 (컷 경계 바로 옆은 디졸브/플래시라 뺀다).
- 표본마다 얼굴(SCRFD/YuNet, 인물 맞춤과 같은 모델)을 찾고, 가장 큰 얼굴로 점수를 낸다.
- 초점: 얼굴을 128px 폭으로 맞춘 뒤 라플라시안 분산. 영상마다 질감(그레인, 소프트 필터)이 달라
  절대값 하나로 자르지 않고, 이 영상에서 가장 선명한 얼굴들(상위 10%) 대비 비율로 본다. 비율이 FOCUS_MIN 미만이면
  초점이 나갔거나 움직여 번진 프레임으로 보고 후보에서 뺀다.
- 같은 장면만 나오지 않게 컷이 다르고 시간이 떨어진 프레임을 고른다.

Qt 에 의존하지 않는다."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import Event

import numpy as np

COVER_SAMPLES = 220  # 영상 전체 표본 수 상한
COVER_WIDTH = 1280  # 분석 폭
FOCUS_MIN = 0.6  # 이 영상의 선명한 얼굴(상위 10%) 대비 이 비율보다 흐리면 제외
FOCUS_ABS_MIN = 12.0  # 이보다 흐리면 영상 전체가 흐려도 제외
FACE_MIN_H = 0.07  # 얼굴 높이가 화면 높이의 이 비율보다 작으면 커버로 쓰기에 작다
YAW_MAX = 0.55  # 이보다 옆을 보면 제외 (정면에 가까운 얼굴)
EDGE_PAD = 2  # 컷 경계 앞뒤로 피하는 프레임 수


@dataclass
class CoverCandidate:
    frame: int
    score: float
    focus: float  # 이 영상 상위 대비 선명도 (1 = 가장 선명한 쪽)
    yaw: float
    face_h: float  # 화면 높이 대비 얼굴 높이
    box: tuple[float, float, float, float]  # 분석 좌표 (COVER_WIDTH 기준)

    def reason(self) -> str:
        side = "정면" if abs(self.yaw) < 0.2 else "살짝 옆"
        size = "얼굴 크게" if self.face_h >= 0.2 else ("얼굴 중간" if self.face_h >= 0.12 else "얼굴 작게")
        return f"{side} · 선명 {int(round(min(1.0, self.focus) * 100))}% · {size}"


def sample_frames(count: int, fps: float, cuts: Sequence = (), limit: int = COVER_SAMPLES) -> list[int]:
    """영상 전체에서 고르게 (0.5초마다, 많으면 limit 개로). 컷 경계 바로 옆 프레임은 뺀다."""
    if count <= 0:
        return []
    step = max(1, int(round(fps / 2)), int(math.ceil(count / max(1, limit))))
    edges = set()
    for c in cuts:
        for b in (int(c.start), int(c.end)):
            edges.update(range(b - EDGE_PAD, b + EDGE_PAD + 1))
    out = [f for f in range(step // 2, count, step) if f not in edges]
    return out[:limit] if len(out) > limit else out


def focus_measure(gray_face: np.ndarray) -> float:
    """얼굴 영역의 선명도: 128px 폭으로 맞춘 뒤 라플라시안 분산 (크기와 무관하게 비교)."""
    import cv2

    if gray_face.size < 64:
        return 0.0
    h, w = gray_face.shape[:2]
    nw = 128
    nh = max(16, int(round(h * nw / max(1, w))))
    g = cv2.resize(gray_face, (nw, nh), interpolation=cv2.INTER_AREA if w > nw else cv2.INTER_CUBIC)
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def _main_face(faces, w: int, h: int):
    """가장 큰 얼굴 (여러 명이면 앞에 있는 주인공)."""
    faces = [f for f in faces if f[2] > f[0] and f[3] > f[1]]
    if not faces:
        return None
    return max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]) * f[4])


@dataclass
class _Raw:
    frame: int
    focus_abs: float
    yaw: float
    face_h: float
    conf: float
    box: tuple[float, float, float, float]
    center_x: float  # 화면 폭 대비 얼굴 가운데
    luma: float


def measure(rgb: np.ndarray, faces) -> _Raw | None:
    """한 프레임의 주인공 얼굴 측정. 얼굴이 없거나 너무 작으면 None."""
    import cv2

    h, w = rgb.shape[:2]
    f = _main_face(faces, w, h)
    if f is None:
        return None
    x1, y1, x2, y2 = (float(v) for v in f[:4])
    face_h = (y2 - y1) / h
    if face_h < FACE_MIN_H:
        return None
    # 얼굴 안쪽만 (상자 가장자리의 배경/머리카락 경계가 선명도를 부풀리지 않게)
    mx, my = 0.15 * (x2 - x1), 0.12 * (y2 - y1)
    crop = rgb[max(0, int(y1 + my)) : min(h, int(y2 - my)), max(0, int(x1 + mx)) : min(w, int(x2 - mx))]
    if crop.size < 64:
        return None
    gray = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2GRAY)
    yaw = float(f[6]) if len(f) > 6 and f[6] is not None else 0.0
    return _Raw(
        0, focus_measure(gray), yaw, face_h, float(f[4]), (x1, y1, x2, y2), (x1 + x2) / 2 / w, float(gray.mean())
    )


def rank(raws: Sequence[_Raw], cuts: Sequence = (), fps: float = 24.0, top: int = 6) -> list[CoverCandidate]:
    """측정값들로 후보 점수를 내고, 컷/시간이 겹치지 않게 top 개를 고른다."""
    if not raws:
        return []
    ref = float(np.percentile([r.focus_abs for r in raws], 90))
    ref = max(ref, FOCUS_ABS_MIN)
    scored: list[CoverCandidate] = []
    for r in raws:
        rel = r.focus_abs / ref
        if rel < FOCUS_MIN or r.focus_abs < FOCUS_ABS_MIN:
            continue  # 초점이 나갔거나 번진 얼굴
        if abs(r.yaw) > YAW_MAX:
            continue
        if not (35 <= r.luma <= 225):
            continue  # 얼굴이 너무 어둡거나 날아감
        frontal = 1.0 - abs(r.yaw) / YAW_MAX * 0.6
        size = min(1.0, r.face_h / 0.25) ** 0.5
        center = 1.0 - 0.4 * min(1.0, abs(r.center_x - 0.5) / 0.5)
        score = min(1.0, rel) ** 1.5 * frontal * size * center * min(1.0, r.conf)
        scored.append(CoverCandidate(r.frame, score, rel, r.yaw, r.face_h, r.box))
    scored.sort(key=lambda c: -c.score)

    def cut_of(f: int):
        for i, c in enumerate(cuts):
            if c.start <= f < c.end:
                return i
        return None

    picked: list[CoverCandidate] = []
    used_cuts: set = set()
    gap = max(1, int(fps * 2))
    for c in scored:
        ci = cut_of(c.frame)
        if ci is not None and ci in used_cuts:
            continue
        if any(abs(c.frame - p.frame) < gap for p in picked):
            continue
        picked.append(c)
        if ci is not None:
            used_cuts.add(ci)
        if len(picked) >= top:
            break
    return picked


def recommend(
    count: int,
    fps: float,
    cuts: Sequence,
    decode: Callable[[int, int, int], np.ndarray],
    aspect: float,
    detector_factory: Callable[[], object] | None = None,
    cancel: Event | None = None,
    progress: Callable[[float], None] | None = None,
    top: int = 6,
) -> list[CoverCandidate]:
    """decode(frame, w, h) -> RGB. aspect = 표시 폭/높이. detector 는 faces(rgb) 를 가진 객체 (reframe.PersonDetector)."""
    from .reframe import PersonDetector

    frames = sample_frames(count, fps, cuts)
    w = COVER_WIDTH
    h = max(2, int(round(w / max(aspect, 1e-6))) // 2 * 2)
    det = (detector_factory or PersonDetector)()
    raws: list[_Raw] = []
    for i, f in enumerate(frames):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("커버 추천을 취소했습니다.")
        rgb = decode(f, w, h)
        r = measure(rgb, det.faces(rgb))
        if r is not None:
            r.frame = f
            raws.append(r)
        if progress:
            progress((i + 1) / max(1, len(frames)))
    return rank(raws, cuts, fps, top)
