"""STORY / REELS 9:16 Fill 의 컷별 가로 위치(리프레이밍).

- 컷마다 가로 위치(pos_x) 하나를 둔다. '컷 안에서 인물 따라가기' 를 켜면, 인물이 컷 안에서 크게 움직이는 컷만
  부드러운 카메라 패닝 경로(cut_path)를 함께 둔다. 세로 위치와 확대는 바꾸지 않는다.
- 자동: 컷 안에서 고르게 뽑은 프레임에서 인물(몸)과 얼굴을 찾고, 9:16 창 안에 인물이 평균적으로 가장 많이 보이는
  가로 위치를 고른다. 인물이 나왔다 사라지는 컷도 평균으로 처리되며, 인물이 없는 컷은 위치를 바꾸지 않는다.
- 수동: 미리보기를 좌우로 끌어 지금 컷의 위치만 바꾼다.

이 모듈은 Qt 에 의존하지 않는다. 검출에는 OpenCV DNN 과 함께 배포하는 ONNX 모델을 쓴다
(얼굴: SCRFD-10G InsightFace 비상업 연구용 라이선스, 없으면 YuNet MIT / 인물: NanoDet-Plus Apache-2.0;
LICENSES/THIRD_PARTY_NOTICES.md).

얼굴 검출 정확도를 위한 처리
- SCRFD 로 640 폭에서 1차 검출 (10px 안팎의 작은 얼굴도 잡음)
- 사람은 있는데 얼굴을 못 찾았거나 얼굴이 아주 작으면, 1280 폭 원본을 좌우 반씩 나눠 2차 검출 (와이드 샷)
- 얼굴 가운데는 상자 가운데가 아니라 코 위치 (옆얼굴에서 상자가 뒤통수 쪽으로 넓어지는 것 보정)
- 크고 선명한 얼굴(앞에 있는 주인공)일수록 가중치를 높게, 흐린 배경 인물은 낮게
- 얼굴을 못 찾은 사람(뒷모습 등)은 몸 상자 대신 머리 위치(몸 상자 위쪽 가운데)로
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event

import numpy as np

from .layout import LayoutParams, compute_geometry, source_bounds


# ====================================================================== 구간 (컷 단위)
@dataclass(frozen=True)
class Segment:
    start: int  # 포함
    end: int  # 미포함
    key: int  # 저장 키: 컷의 시작 프레임 (컷이 없으면 -1 = 구간 전체)


def segments(sel_start: int, sel_end: int, cuts: Sequence) -> list[Segment]:
    """선택 구간을 컷 경계로 나눈다. 컷에 속하지 않는 곳(컷 정보 없음 포함)은 key -1 조각."""
    out: list[Segment] = []
    cur = sel_start
    for c in sorted(cuts, key=lambda c: c.start):
        a, b = max(cur, c.start), min(sel_end, c.end)
        if b <= a:
            continue
        if a > cur:
            out.append(Segment(cur, a, -1))
        out.append(Segment(a, b, int(c.start)))
        cur = b
    if cur < sel_end:
        out.append(Segment(cur, sel_end, -1))
    return out


def pos_for_frame(
    frame: int, cuts: Sequence, cut_x: dict[int, float], default: float, cut_path: dict[int, list] | None = None
) -> float:
    """frame 이 속한 컷의 가로 위치 (지정 안 됐으면 default). 패닝 경로가 있는 컷은 그 프레임의 경로 위치."""
    if not cut_x and not cut_path:
        return default
    for c in cuts:
        if c.start <= frame < c.end:
            path = (cut_path or {}).get(int(c.start))
            if path:
                return path_pos(path, frame - c.start)
            return cut_x.get(int(c.start), default)
    path = (cut_path or {}).get(-1)
    if path:
        return path_pos(path, frame)
    return cut_x.get(-1, default)


def path_pos(path: Sequence[Sequence[float]], offset: float) -> float:
    """패닝 경로 [(컷 시작 기준 프레임, pos_x), ...] 에서 offset 의 위치 (키 사이는 직선, 앞뒤는 끝값)."""
    if offset <= path[0][0]:
        return float(path[0][1])
    for (a, pa), (b, pb) in zip(path, path[1:]):
        if offset <= b:
            return float(pa + (pb - pa) * (offset - a) / max(1e-9, b - a))
    return float(path[-1][1])


def crop_steps(
    disp_w: int,
    disp_h: int,
    layout: LayoutParams,
    segs: Sequence[Segment],
    cut_x: dict[int, float],
    cut_path: dict[int, list] | None = None,
) -> list[tuple[int, int]]:
    """렌더링용 [(선택 시작 기준 프레임 오프셋, 표시 좌표 crop_x)]. Fill 이 아니거나 컷별 위치가 없으면 빈 목록.
    패닝 경로가 있는 컷은 경로의 키마다 한 단계씩 넣는다 (렌더는 단계 사이를 직선으로 이어 부드럽게 움직인다:
    단계에 'lerp' 표시가 있는 구간만). 반환 형식은 [(n, x)] 또는 [(n, x, 1)] (다음 단계까지 직선으로 이어짐)."""
    if layout.mode != "fill" or not segs or not (cut_x or cut_path):
        return []
    cut_path = cut_path or {}

    def xpos(px: float) -> int:
        return compute_geometry(disp_w, disp_h, "916", replace(layout, pos_x=px)).crop_x

    base = segs[0].start
    steps: list[tuple] = []
    for s in segs:
        path = cut_path.get(s.key)
        if path and len(path) > 1:
            for off, px in path:
                n = s.start + int(off)
                if n >= s.end:
                    break
                if n < s.start:
                    continue
                steps.append((n - base, xpos(px), 1))  # 다음 단계까지 직선으로
            # 컷 끝까지 경로의 마지막 값 (다음 컷 첫 단계에서 끊긴다)
            last = steps[-1]
            steps[-1] = (last[0], last[1])
            continue
        x = xpos(cut_x.get(s.key, layout.pos_x))
        if not steps or len(steps[-1]) == 3 or steps[-1][1] != x:
            steps.append((s.start - base, x))
    return steps


# ====================================================================== 컷 안에서 인물 따라가기 (패닝)
PAN_MIN_TRAVEL = 0.08  # 부드럽게 다듬은 경로가 창 폭의 이 비율보다 적게 움직이면 패닝하지 않는다 (흔들림 무시)
PAN_SMOOTH_SEC = 0.45  # 경로를 다듬는 정도 (가우스 시그마, 초). 클수록 느긋한 카메라
PAN_MAX_SPEED = 0.55  # 최대 속도 (창 폭/초)
PAN_MAX_ACCEL = 0.9  # 최대 가속도 (창 폭/초²): 출발과 멈춤이 툭 끊기지 않게
PAN_KEY_STEP = 2  # 경로 키 간격 (프레임)


def _gauss(y: np.ndarray, sigma: float) -> np.ndarray:
    """끝값을 이어 붙인 가우스 다듬기 (앞뒤 지연 없음)."""
    if sigma <= 0.5 or len(y) < 3:
        return y.copy()
    r = int(math.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.concatenate([np.full(r, y[0]), y, np.full(r, y[-1])])
    return np.convolve(pad, k, mode="valid")


def _limit_motion(y: np.ndarray, vmax: float, amax: float) -> np.ndarray:
    """프레임당 최대 속도/가속도 제한. 앞으로 한 번, 뒤로 한 번 따라가며 평균해 한쪽으로 밀리지 않게."""

    def follow(seq: np.ndarray) -> np.ndarray:
        out = np.empty_like(seq)
        out[0], v = seq[0], 0.0
        for i in range(1, len(seq)):
            want = seq[i] - out[i - 1]
            # 멈춰야 할 거리 안에 들어오면 미리 감속 (v²/2a)
            stop = v * v / (2 * amax) if amax > 0 else 0.0
            if abs(want) <= stop + 1e-12 and np.sign(want) == np.sign(v):
                target_v = math.copysign(math.sqrt(max(0.0, 2 * amax * abs(want))), want)
            else:
                target_v = max(-vmax, min(vmax, want))
            dv = max(-amax, min(amax, target_v - v))
            v = max(-vmax, min(vmax, v + dv))
            out[i] = out[i - 1] + v
        return out

    fwd = follow(y)
    bwd = follow(y[::-1])[::-1]
    return (fwd + bwd) / 2


def pan_path(
    samples: Sequence[tuple[int, Sequence[Det]]],
    seg: Segment,
    lo: float,
    hi: float,
    win_w: float,
    fps: float,
) -> list[list[float]] | None:
    """컷 안 표본 [(프레임, 검출)] 로 부드러운 패닝 경로 [[컷 시작 기준 프레임, pos_x], ...].
    인물이 거의 움직이지 않으면(다듬은 경로의 이동이 PAN_MIN_TRAVEL 미만) None (고정 위치가 낫다).

    1) 표본마다 best_window_x 와 같은 기준(옆얼굴 보정, 여러 명이면 무리)으로 인물 가운데를 구한다.
       컷 전체의 주인공 무리(g)에 가장 가까운 후보를 골라, 다른 사람에게 튀지 않게 한다.
    2) 세 표본 중앙값으로 튀는 값을 지우고, 프레임마다 직선으로 이은 뒤 가우스로 다듬는다 (앞뒤 지연 없음).
    3) 실제 카메라처럼 속도/가속도를 제한한다 (출발과 멈춤이 부드럽게)."""
    span = hi - lo - win_w
    n = seg.end - seg.start
    if span <= 1 or n < max(8, int(fps * 0.6)) or len(samples) < 4:
        return None
    frames = _head_frames([d for _f, d in samples])
    allp = [d for fr in frames for d in fr if d.x2 > d.x1]
    if not allp:
        return None
    xs = lo + np.linspace(0.0, span, 97)
    cands = _subject_centers(allp, xs, win_w)
    if not cands:
        return None
    g = cands[int(np.argmax([c[1] for c in cands]))][0]
    pts: list[tuple[int, float]] = []
    for (f, _d), fr in zip(samples, frames):
        fr = [d for d in fr if d.x2 > d.x1]
        if not fr:
            continue
        cs = _subject_centers(fr, xs, win_w)
        if cs:
            c0 = min((c[0] for c in cs), key=lambda c: abs(c - g))
            pts.append((int(f) - seg.start, min(max(c0 - win_w / 2, lo), lo + span)))
    if len(pts) < 4:
        return None
    pts.sort()
    t = np.array([p[0] for p in pts], dtype=float)
    x = np.array([p[1] for p in pts], dtype=float)
    if len(x) >= 3:  # 튀는 값 제거
        x = np.array([np.median(x[max(0, i - 1) : i + 2]) for i in range(len(x))])
    frames_t = np.arange(n, dtype=float)
    y = np.interp(frames_t, t, x)  # 첫/끝 표본 밖은 끝값
    y = _gauss(y, PAN_SMOOTH_SEC * fps)
    y = _limit_motion(y, PAN_MAX_SPEED * win_w / fps, PAN_MAX_ACCEL * win_w / fps / fps)
    y = _gauss(y, 0.12 * fps)  # 제한하며 생긴 작은 꺾임을 다시 다듬기
    y = np.clip(y, lo, lo + span)
    if float(y.max() - y.min()) < PAN_MIN_TRAVEL * win_w:
        return None
    pos = (y - lo) / span
    keys = list(range(0, n, PAN_KEY_STEP))
    if keys[-1] != n - 1:
        keys.append(n - 1)
    return [[int(k), round(float(pos[k]), 4)] for k in keys]


# ====================================================================== 가로 위치 고르기
@dataclass
class Det:
    x1: float
    y1: float
    x2: float
    y2: float
    weight: float  # 검출 신뢰도 × 크기 (얼굴은 가중)
    face: bool = False
    yaw: float = 0.0  # 얼굴이 돌아간 정도와 방향 (-1 왼쪽을 봄 .. 0 정면 .. +1 오른쪽을 봄, 화면 기준)


FACE_WEIGHT = 1.6  # 얼굴이 몸보다 중요하다 (몸이 잘려도 얼굴이 보이는 편이 낫다)
SCORE_TOLERANCE = 0.97  # 최고 점수의 이 비율 안이면 같은 노출로 보고, 그중 인물 무리 가운데에 가장 가까운 위치
FACE_CUT_PENALTY = 0.6  # 창 가장자리에 얼굴이 반쯤 걸리면 감점 (얼굴은 다 넣거나 아예 빼는 편이 낫다)
NUDGE = 0.15  # 평균 위치에서 얼굴이 잘리지 않게 옮겨 볼 수 있는 범위 (창 폭 대비)
HEAD_SHIFT = 0.25  # 옆을 보는 얼굴: 기준을 코에서 뒤통수 쪽으로 옮기는 양 (얼굴 폭 대비, 완전 옆얼굴 기준)


def yaw_from_landmarks(eye_a_x: float, eye_b_x: float, nose_x: float, face_w: float) -> float:
    """눈 두 점과 코의 가로 위치로 얼굴이 돌아간 방향. 정면이면 코가 두 눈 가운데, 옆을 보면 코가 보는 쪽으로 치우친다.
    반환 -1..1 (+: 화면 오른쪽을 봄)."""
    mid = (eye_a_x + eye_b_x) / 2
    span = max(abs(eye_a_x - eye_b_x), 0.25 * max(face_w, 1e-6))
    return float(max(-1.0, min(1.0, (nose_x - mid) / span / 0.9)))


def face_anchor_x(x1: float, x2: float, yaw: float) -> float:
    """얼굴을 맞출 가로 기준. 정면이면 코(상자 가운데), 옆을 보면 보는 반대쪽(뒤통수 쪽)으로 옮긴다.
    옆얼굴의 코는 머리 앞쪽 끝이라, 코에 맞추면 얼굴 앞에 빈 곳이 생기고 뒷머리가 잘린다."""
    return (x1 + x2) / 2 - yaw * HEAD_SHIFT * (x2 - x1)


def _head_frames(frames: Sequence[Sequence[Det]]) -> list[list[Det]]:
    """얼굴 상자를 face_anchor_x 가 가운데가 되도록 옮긴 사본 (노출/잘림 계산도 머리 전체 기준으로)."""
    out = []
    for fr in frames:
        row = []
        for d in fr:
            if d.face and d.yaw:
                s = face_anchor_x(d.x1, d.x2, d.yaw) - (d.x1 + d.x2) / 2
                d = replace(d, x1=d.x1 + s, x2=d.x2 + s)
            row.append(d)
        out.append(row)
    return out


def _cover(dets: Sequence[Det], xs: np.ndarray, win_w: float):
    x1 = np.array([d.x1 for d in dets])
    x2 = np.array([d.x2 for d in dets])
    ov = np.clip(np.minimum(x2[None, :], xs[:, None] + win_w) - np.maximum(x1[None, :], xs[:, None]), 0, None)
    return ov / (x2 - x1)[None, :], x1, x2


def _window_scores(dets: Sequence[Det], xs: np.ndarray, win_w: float):
    """창 위치마다의 점수. 몸은 보이는 폭 비율만큼, 얼굴은 거의 다 보여야 점수가 되고 반쯤 잘리면 감점."""
    cover, x1, x2 = _cover(dets, xs, win_w)
    w = np.array([d.weight for d in dets])
    face = np.array([d.face for d in dets])
    eff = np.where(face[None, :], cover**4 - FACE_CUT_PENALTY * 4 * cover * (1 - cover), cover)
    contrib = eff * w[None, :]
    # 가운데 계산용: 얼굴이 (절반 이상) 보이면 얼굴만으로, 얼굴이 없으면(뒷모습 등) 몸으로.
    # 몸 상자는 팔, 머리카락, 기울어진 자세 때문에 얼굴과 중심이 어긋나기 쉽다.
    seen_face = np.where(face[None, :] & (cover >= 0.5), cover * w[None, :], 0.0)
    seen_body = np.where(~face[None, :], np.clip(cover, 0, 1) * w[None, :], 0.0)
    has_face = seen_face.sum(axis=1, keepdims=True) > 0
    seen = np.where(has_face, seen_face, seen_body)
    return contrib.sum(axis=1), seen, (x1 + x2) / 2


def _face_cut_cost(frames: Sequence[Sequence[Det]], xs: np.ndarray, win_w: float) -> np.ndarray:
    """창 위치마다 '반쯤 잘린 얼굴' 의 합 (모든 표본 프레임)."""
    faces = [d for fr in frames for d in fr if d.face and d.x2 > d.x1]
    if not faces:
        return np.zeros(len(xs))
    cover, _x1, _x2 = _cover(faces, xs, win_w)
    w = np.array([d.weight for d in faces])
    return (4 * cover * (1 - cover) * w[None, :]).sum(axis=1)


def _subject_centers(dets: Sequence[Det], xs: np.ndarray, win_w: float) -> list[tuple[float, float]]:
    """노출이 최고(허용 오차 안)인 창들이 담는 인물 무리의 가중 중심 목록 [(중심, 점수)]."""
    score, cw, cx = _window_scores(dets, xs, win_w)
    if score.max() <= 1e-9:
        return []
    out = []
    for i in np.where(score >= score.max() * SCORE_TOLERANCE)[0]:
        if cw[i].sum() > 0:
            out.append((float((cx * cw[i]).sum() / cw[i].sum()), float(score[i])))
    return out


DWELL_MARGIN = 0.1  # 창 양쪽 끝 이 비율 안쪽에 인물 가운데가 들어와야 '잘 보인다' 로 센다
DWELL_MAJORITY = 0.55  # 한 자리에 이 비율보다 오래 머물렀을 때만 그쪽으로 (아니면 경로 평균)


def _dwell_center(centers: Sequence[float], lo: float, span: float, win_w: float, steps: int = 96) -> float:
    """인물(얼굴)이 가장 오래 머문 쪽의 가운데.
    표본은 시간상 고르게 뽑았으므로 '창 안쪽에 가운데가 들어오는 표본 수' = 머문 시간이다.
    그 수가 전체의 절반을 넘으면, 그런 창들 중 들어온 표본들의 평균이 전체 평균에 가장 가까운 창을 고르고 그 평균을 돌려준다.
    한쪽에 오래 있다 잠깐 이동하면 오래 있던 쪽으로, 컷 내내 고르게 움직이면 경로의 가운데로 맞춰진다."""
    c = np.asarray(centers, dtype=float)
    overall = float(c.mean())
    inner = win_w * (1 - 2 * DWELL_MARGIN)
    xs = lo + np.linspace(0.0, max(0.0, span), steps + 1) + win_w * DWELL_MARGIN  # 안쪽 구간의 왼쪽 끝
    inside = (c[None, :] >= xs[:, None]) & (c[None, :] <= xs[:, None] + inner)
    count = inside.sum(axis=1)
    if count.max() <= DWELL_MAJORITY * len(c):  # 오래 머문 자리가 따로 없다 (고르게 움직임 등): 경로 평균
        return overall
    best, best_d = overall, math.inf
    for i in np.where(count == count.max())[0]:
        m = float(c[inside[i]].mean())
        d = abs(m - overall)
        if d < best_d:
            best, best_d = m, d
    return best


def best_window_x(frames: Sequence[Sequence[Det]], lo: float, hi: float, win_w: float, steps: int = 96) -> float | None:
    """창 [x, x+win_w] 의 x 를 [lo, hi-win_w] 에서 고른다.

    1) 컷 전체로 보아 인물이 가장 많이 보이는 쪽(여러 명이면 여러 명이 모인 쪽)을 정한다.
    2) 표본 프레임마다 그쪽 인물(무리)의 가운데를 구한다. 같은 노출의 후보가 여럿이면 1) 에 가까운 쪽.
    3) 프레임별 가운데 중 가장 오래 머문 쪽(머문 표본들의 평균)에 창 가운데를 맞춘다.
       한쪽에 오래 있다 잠깐 움직이면 오래 있던 쪽, 컷 내내 고르게 움직이면 경로의 가운데.
    인물이 하나도 없으면 None (위치를 바꾸지 않음). 반환: 0..1 위치 (pos_x)."""
    span = hi - lo - win_w
    frames = _head_frames(frames)
    allp = [d for fr in frames for d in fr if d.x2 > d.x1]
    if not allp:
        return None
    if span <= 0.5:
        return 0.5
    xs = lo + np.linspace(0.0, span, steps + 1)
    # 1) 컷 전체 기준 (프레임마다 따로 보지 않고 합친 노출)
    cands = _subject_centers(allp, xs, win_w)
    if not cands:
        return None
    g = cands[int(np.argmax([c[1] for c in cands]))][0]
    # 2) 프레임별 인물 가운데
    centers = []
    for fr in frames:
        fr = [d for d in fr if d.x2 > d.x1]
        if not fr:
            continue
        cs = _subject_centers(fr, xs, win_w)
        if cs:
            c0 = min((c[0] for c in cs), key=lambda c: abs(c - g))
            centers.append(c0)
    center = _dwell_center(centers, lo, span, win_w) if centers else g
    # 3) 그 위치를 창 가운데로
    x = min(max(center - win_w / 2, lo), lo + span)
    # 4) 그 위치에서 얼굴이 창 가장자리에 걸리면, 가까운 범위 안에서 얼굴이 덜 잘리는 위치로 살짝 옮긴다
    near = np.linspace(max(lo, x - NUDGE * win_w), min(lo + span, x + NUDGE * win_w), 61)
    cost = _face_cut_cost(frames, near, win_w)
    if len(cost) and cost.max() > 1e-9:
        here = float(_face_cut_cost(frames, np.array([x]), win_w)[0])
        best = cost.min()
        if here - best > 1e-6:
            ok = np.where(cost <= best + 0.02 * max(best, 1e-3))[0]
            x = float(near[min(ok, key=lambda i: abs(near[i] - x))])
    return float((x - lo) / span)


# ====================================================================== 인물 / 얼굴 검출
def models_dir() -> Path:
    from .. import paths

    return Path(paths.resource_path("assets/models"))


NANODET_FILE = "object_detection_nanodet_2022nov.onnx"
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SCRFD_FILE = "face_detection_scrfd_10g.onnx"

# 얼굴 한 개: (x1, y1, x2, y2, 신뢰도, 코 x 또는 None)
Face = tuple


class FaceSCRFD:
    """SCRFD-10G (InsightFace) 얼굴 검출. 입력 RGB uint8. 폭을 맞춰 줄이고 32 배수로 채워 넣는다 (정사각형 아님: 빠름)."""

    STRIDES = (8, 16, 32)
    TH = 0.5

    def __init__(self, path: Path):
        import cv2

        self.cv2 = cv2
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.names = self.net.getUnconnectedOutLayersNames()

    def detect(self, rgb: np.ndarray, width: int = 640) -> list[Face]:
        cv2 = self.cv2
        h, w = rgb.shape[:2]
        s = min(1.0, width / w) if w > width else width / w
        nw, nh = max(32, int(round(w * s))), max(32, int(round(h * s)))
        W, H = (nw + 31) // 32 * 32, (nh + 31) // 32 * 32
        canvas = np.zeros((H, W, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        blob = cv2.dnn.blobFromImage(canvas, 1 / 128.0, (W, H), (127.5, 127.5, 127.5), swapRB=False)
        self.net.setInput(blob)
        outs = [o.reshape(-1, o.shape[-1]) for o in self.net.forward(self.names)]
        # 출력 순서는 버전마다 다를 수 있어 모양(열 수)과 크기로 나눈다
        sc = sorted([o for o in outs if o.shape[1] == 1], key=lambda o: -o.shape[0])
        bb = sorted([o for o in outs if o.shape[1] == 4], key=lambda o: -o.shape[0])
        kp = sorted([o for o in outs if o.shape[1] == 10], key=lambda o: -o.shape[0])
        boxes, scores, noses = [], [], []
        for st, s_, b_, k_ in zip(self.STRIDES, sc, bb, kp):
            gh, gw = H // st, W // st
            gy, gx = np.mgrid[:gh, :gw]
            c = np.repeat(np.stack([gx, gy], -1).reshape(-1, 2).astype(np.float32) * st, 2, axis=0)
            if c.shape[0] != s_.shape[0]:
                continue
            m = s_[:, 0] >= self.TH
            if not m.any():
                continue
            d, cc = b_[m] * st, c[m]
            boxes.append(
                np.column_stack([cc[:, 0] - d[:, 0], cc[:, 1] - d[:, 1], cc[:, 0] + d[:, 2], cc[:, 1] + d[:, 3]])
            )
            scores.append(s_[m, 0])
            noses.append(
                np.column_stack([cc[:, 0] + k_[m, 0] * st, cc[:, 0] + k_[m, 2] * st, cc[:, 0] + k_[m, 4] * st])
            )
            # 다섯 점(눈, 눈, 코, 입, 입) 중 눈 두 점과 코의 x
        if not boxes:
            return []
        B, S, K = np.concatenate(boxes) / s, np.concatenate(scores), np.concatenate(noses) / s
        keep = cv2.dnn.NMSBoxes(np.column_stack([B[:, :2], B[:, 2:] - B[:, :2]]).tolist(), S.tolist(), self.TH, 0.4)
        out = []
        for i in np.array(keep).ravel():
            x1, y1, x2, y2 = (float(v) for v in B[int(i)])
            ea, eb, nx = (float(v) for v in K[int(i)])
            yaw = yaw_from_landmarks(ea, eb, nx, x2 - x1)
            out.append((max(0.0, x1), max(0.0, y1), min(float(w), x2), min(float(h), y2), float(S[int(i)]),
                        nx if x1 <= nx <= x2 else None, yaw))  # fmt: skip
        return out


def _nms_faces(faces: list[Face], iou: float = 0.4) -> list[Face]:
    faces = sorted(faces, key=lambda f: -f[4])
    out: list[Face] = []
    for f in faces:
        ok = True
        for g in out:
            ix = max(0.0, min(f[2], g[2]) - max(f[0], g[0]))
            iy = max(0.0, min(f[3], g[3]) - max(f[1], g[1]))
            inter = ix * iy
            u = (f[2] - f[0]) * (f[3] - f[1]) + (g[2] - g[0]) * (g[3] - g[1]) - inter
            if u > 0 and inter / u > iou:
                ok = False
                break
        if ok:
            out.append(f)
    return out


def head_box(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
    """사람 상자에서 머리가 있을 만한 곳 (위쪽 가운데). 팔을 벌리거나 기울어도 몸 상자 가운데보다 얼굴에 가깝다."""
    bw, bh = x2 - x1, y2 - y1
    hw = max(0.3 * bw, 0.12 * bh)
    cx = (x1 + x2) / 2
    return cx - hw / 2, y1, cx + hw / 2, y1 + 0.22 * bh


class PersonDetector:
    """NanoDet-Plus(COCO 'person') + 얼굴(SCRFD, 없으면 YuNet). 입력은 RGB uint8 HxWx3.
    스레드마다 하나씩 만든다 (cv2.dnn 객체는 공유 불가)."""

    SIZE = 416
    STRIDES = (8, 16, 32)
    REG_MAX = 7
    PERSON_TH = 0.35
    FACE_TH = 0.6
    FACE_W1 = 640  # 1차 얼굴 검출 폭
    SMALL_FACE = 18  # 1차에서 이보다 작은(px, 640 폭 기준) 얼굴만 있으면 2차 검출

    def __init__(self, model_dir: Path | None = None):
        import cv2

        try:  # 백엔드 선택 경고 같은 무해한 로그는 숨긴다
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:  # noqa: BLE001
            pass
        self.cv2 = cv2
        d = Path(model_dir or models_dir())
        self.net = cv2.dnn.readNet(str(d / NANODET_FILE))
        self.scrfd: FaceSCRFD | None = None
        if (d / SCRFD_FILE).is_file():
            try:
                self.scrfd = FaceSCRFD(d / SCRFD_FILE)
            except Exception:  # noqa: BLE001  (이 OpenCV 에서 못 읽으면 YuNet 으로)
                self.scrfd = None
        self.face = cv2.FaceDetectorYN.create(str(d / YUNET_FILE), "", (320, 320), self.FACE_TH, 0.3, 50)
        self.mean = np.array([103.53, 116.28, 123.675], np.float32)
        self.std = np.array([57.375, 57.12, 58.395], np.float32)
        self.anchors = []
        for st in self.STRIDES:
            n = self.SIZE // st
            gx, gy = np.meshgrid(np.arange(n) * st, np.arange(n) * st)
            self.anchors.append(np.column_stack([gx.ravel() + 0.5 * (st - 1), gy.ravel() + 0.5 * (st - 1)]))

    @property
    def face_model(self) -> str:
        return "scrfd" if self.scrfd is not None else "yunet"

    def persons(self, rgb: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        cv2 = self.cv2
        h, w = rgb.shape[:2]
        s = self.SIZE / max(w, h)
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        canvas = np.zeros((self.SIZE, self.SIZE, 3), np.uint8)
        top = (self.SIZE - nh) // 2
        canvas[top : top + nh, :nw] = cv2.resize(rgb[:, :, ::-1], (nw, nh), interpolation=cv2.INTER_AREA)
        blob = cv2.dnn.blobFromImage((canvas.astype(np.float32) - self.mean) / self.std)
        self.net.setInput(blob)
        outs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        # 출력 순서는 OpenCV 버전마다 다르므로 모양으로 나눈다: 분류 (N, 80), 상자 분포 (N, 4*(REG_MAX+1))
        cls_o = sorted([o for o in outs if o.shape[-1] == 80], key=lambda o: -o.shape[-2])
        reg_o = sorted([o for o in outs if o.shape[-1] == 4 * (self.REG_MAX + 1)], key=lambda o: -o.shape[-2])
        boxes, scores = [], []
        for st, cls, reg, anc in zip(self.STRIDES, cls_o, reg_o, self.anchors):
            cls = cls.reshape(-1, cls.shape[-1])
            if cls.shape[0] != anc.shape[0]:
                continue
            e = np.exp(reg.reshape(-1, self.REG_MAX + 1))
            dist = ((e / e.sum(1, keepdims=True)) @ np.arange(self.REG_MAX + 1)).reshape(-1, 4) * st
            boxes.append(
                np.column_stack(
                    [anc[:, 0] - dist[:, 0], anc[:, 1] - dist[:, 1], anc[:, 0] + dist[:, 2], anc[:, 1] + dist[:, 3]]
                )
            )
            scores.append(cls[:, 0])  # COCO 0 = person
        if not boxes:
            return []
        b, sc = np.concatenate(boxes), np.concatenate(scores)
        m = sc >= self.PERSON_TH
        b, sc = b[m], sc[m]
        if not len(sc):
            return []
        keep = cv2.dnn.NMSBoxes(
            np.column_stack([b[:, :2], b[:, 2:] - b[:, :2]]).tolist(), sc.tolist(), self.PERSON_TH, 0.6
        )
        out = []
        for i in np.array(keep).ravel():
            x1, y1, x2, y2 = b[int(i)]
            out.append(
                (
                    max(0.0, x1 / s),
                    max(0.0, (y1 - top) / s),
                    min(float(w), x2 / s),
                    min(float(h), (y2 - top) / s),
                    float(sc[int(i)]),
                )
            )
        return out

    def _yunet(self, rgb: np.ndarray) -> list[Face]:
        h, w = rgb.shape[:2]
        self.face.setInputSize((w, h))
        _, f = self.face.detect(np.ascontiguousarray(rgb[:, :, ::-1]))
        if f is None:
            return []
        # YuNet 랜드마크: 4,5 눈 / 6,7 눈 / 8,9 코
        return [
            (float(r[0]), float(r[1]), float(r[0] + r[2]), float(r[1] + r[3]), float(r[14]), float(r[8]),
             yaw_from_landmarks(float(r[4]), float(r[6]), float(r[8]), float(r[2])))
            for r in f
        ]  # fmt: skip

    def faces(self, rgb: np.ndarray, persons: Sequence | None = None) -> list[Face]:
        """1차: 640 폭. 사람은 있는데 그 머리 자리에 얼굴이 없거나 얼굴이 아주 작으면 2차: 좌우 반쪽을 원본 해상도로."""
        h, w = rgb.shape[:2]
        if self.scrfd is None:
            return self._yunet(rgb)
        first = self.scrfd.detect(rgb, self.FACE_W1)
        k = self.FACE_W1 / w
        need = False
        if persons:
            for px1, py1, px2, py2, _c in persons:
                hx1, hy1, hx2, hy2 = head_box(px1, py1, px2, py2)
                if not any(hx1 - 0.5 * (hx2 - hx1) <= (f[0] + f[2]) / 2 <= hx2 + 0.5 * (hx2 - hx1) for f in first):
                    need = True
                    break
        if first and min((f[2] - f[0]) * k for f in first) < self.SMALL_FACE:
            need = True
        if not need or w <= self.FACE_W1 * 1.2:
            return first
        more: list[Face] = []
        half = int(w * 0.55)
        for x0 in (0, w - half):
            tile = rgb[:, x0 : x0 + half]
            for f in self.scrfd.detect(tile, tile.shape[1]):
                more.append((f[0] + x0, f[1], f[2] + x0, f[3], f[4], None if f[5] is None else f[5] + x0, *f[6:7]))
        return _nms_faces(first + more)

    def _sharpness(self, rgb: np.ndarray, f: Face) -> float:
        x1, y1, x2, y2 = (int(round(v)) for v in f[:4])
        crop = rgb[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)]
        if crop.size < 16:
            return 0.0
        g = self.cv2.cvtColor(np.ascontiguousarray(crop), self.cv2.COLOR_RGB2GRAY)
        if g.shape[0] > 64:  # 크기와 무관하게 비교되도록 맞춘다
            g = self.cv2.resize(g, (64, max(8, int(64 * g.shape[0] / max(1, g.shape[1])))))
        return float(self.cv2.Laplacian(g, self.cv2.CV_32F).var())

    def detect(self, rgb: np.ndarray) -> list[Det]:
        """프레임 크기로 정규화한 가중치: 신뢰도 × sqrt(면적 비율). 큰(가까운) 인물이 주인공이 된다.
        얼굴은 선명할수록(초점이 맞은 주인공) 가중치가 크고, 가운데는 코 위치. 사람은 머리 자리 상자로."""
        h, w = rgb.shape[:2]
        area = float(w * h)
        persons = self.persons(rgb)
        out = []
        for x1, y1, x2, y2, c in persons:
            hx1, hy1, hx2, hy2 = head_box(x1, y1, x2, y2)
            out.append(Det(hx1, hy1, hx2, hy2, c * math.sqrt(max(0.0, (x2 - x1) * (y2 - y1)) / area)))
        faces = self.faces(rgb, persons)
        sharp = [self._sharpness(rgb, f) for f in faces]
        top = max(sharp) if sharp else 0.0
        for f, sh in zip(faces, sharp):
            x1, y1, x2, y2, c, nose = f[:6]
            yaw = float(f[6]) if len(f) > 6 else 0.0
            if nose is not None:  # 상자 가운데 대신 코 위치를 가운데로 (옆얼굴 보정, 너비는 그대로)
                half = (x2 - x1) / 2
                x1, x2 = nose - half, nose + half
            sf = 0.45 + 0.55 * min(1.0, sh / top) if top > 1e-6 else 1.0
            wgt = FACE_WEIGHT * c * math.sqrt(max(0.0, (x2 - x1) * (y2 - y1)) / area) * 3.0 * sf
            out.append(Det(x1, y1, x2, y2, wgt, True, yaw))
        return out


# ====================================================================== 분석 (원본 디코딩 + 검출)
ANALYSIS_WIDTH = 1280  # 검출용 축소 폭. 1차 검출은 640 으로 줄여서, 작은 얼굴 2차 검출은 이 해상도로
SAMPLES_PER_SEC = 3.0
MIN_SAMPLES, MAX_SAMPLES = 3, 8  # 컷 하나당 표본 수 (인물 이동을 평균하려면 고르게 여러 장)
MAX_TOTAL_SAMPLES = 320
# 패닝: 움직임을 따라가려면 더 촘촘히 (1초에 6장, 컷당 최대 36장, 전체 최대 1200장)
PAN_SAMPLES_PER_SEC, PAN_MAX_SAMPLES, PAN_MAX_TOTAL = 6.0, 36, 1200


def sample_frames(seg: Segment, fps: float, budget_scale: float = 1.0, pan: bool = False) -> list[int]:
    """컷 안에서 고르게 뽑는다. 컷 경계 바로 옆 프레임(디졸브/플래시)은 피한다."""
    n = seg.end - seg.start
    if n <= 0:
        return []
    per_sec, most = (PAN_SAMPLES_PER_SEC, PAN_MAX_SAMPLES) if pan else (SAMPLES_PER_SEC, MAX_SAMPLES)
    k = int(round(n / max(1.0, fps) * per_sec * budget_scale))
    k = max(MIN_SAMPLES, min(most, k))
    pad = min(2, (n - 1) // 4)
    a, b = seg.start + pad, seg.end - 1 - pad
    if b <= a:
        return [seg.start + n // 2]
    return sorted({int(round(a + (b - a) * (i + 0.5) / k)) for i in range(k)})


def analyze(
    info,
    index,
    layout: LayoutParams,
    segs: Sequence[Segment],
    decode: Callable[[int, int, int], np.ndarray],
    cancel: Event | None = None,
    progress: Callable[[float], None] | None = None,
    detector_factory: Callable[[], PersonDetector] | None = None,
    workers: int = 3,
    cache: dict[int, list[Det]] | None = None,
    pan: bool = False,
    paths_out: dict[int, list] | None = None,
) -> dict[int, float]:
    """컷마다 인물이 가장 많이 보이는 가로 위치. 반환 {컷 키: pos_x} (인물을 못 찾은 컷은 빠진다).
    decode(frame, w, h) -> RGB ndarray (표시 좌표 전체 프레임을 w×h 로 축소).
    cache: {원본 프레임 번호: 검출 목록(표시 좌표)}. 있는 프레임은 다시 디코딩/검출하지 않고, 새로 검출한 것은 채워 넣는다
    (STORY 와 REELS, 다시 누르기, 컷 하나만 맞추기가 같은 검출 결과를 함께 쓴다).
    pan: 컷 안에서 인물 따라가기. 표본을 촘촘히 뽑고, 인물이 크게 움직인 컷의 패닝 경로를 paths_out 에 채운다."""
    lp = replace(layout, mode="fill")
    dw, dh = info.display_w, info.display_h
    geo = compute_geometry(dw, dh, "916", lp)
    bx, by, bw, bh = source_bounds(dw, dh, lp) or (0, 0, dw, dh)
    if bw - geo.crop_w <= 2:  # 가로로 움직일 여지가 없다 (세로 영상 등)
        return {}
    fps = float(index.fps) if getattr(index, "fps", None) else 24.0
    total = sum(len(sample_frames(s, fps, pan=pan)) for s in segs)
    scale = min(1.0, (PAN_MAX_TOTAL if pan else MAX_TOTAL_SAMPLES) / max(1, total))
    plan = [(s, sample_frames(s, fps, scale, pan=pan)) for s in segs]
    cache = {} if cache is None else cache
    per_seg: dict[int, list[tuple[int, list[Det]]]] = {i: [] for i in range(len(plan))}
    jobs = []
    for i, (_s, fs) in enumerate(plan):
        for f in fs:
            if f in cache:
                per_seg[i].append((f, cache[f]))
            else:
                jobs.append((i, f))
    aw = ANALYSIS_WIDTH
    ah = max(2, int(round(aw * dh / dw)) // 2 * 2)
    k = aw / dw  # 표시 좌표 -> 분석 좌표
    factory = detector_factory or PersonDetector
    import threading

    local = threading.local()

    def work(job):
        if cancel is not None and cancel.is_set():
            raise InterruptedError("인물 분석을 취소했습니다.")
        det = getattr(local, "det", None)
        if det is None:
            det = local.det = factory()
        img = decode(job[1], aw, ah)
        return job[0], det.detect(img)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for (i, dets), (_i, f) in zip(ex.map(work, jobs), jobs):
            fr = [Det(d.x1 / k, d.y1 / k, d.x2 / k, d.y2 / k, d.weight, d.face, d.yaw) for d in dets]
            cache[f] = fr
            per_seg[i].append((f, fr))
            done += 1
            if progress:
                progress(done / max(1, len(jobs)))
    if progress and not jobs:
        progress(1.0)
    out: dict[int, float] = {}
    for i, (s, _fs) in enumerate(plan):
        samples = sorted(per_seg[i], key=lambda x: x[0])
        px = best_window_x([d for _f, d in samples], bx, bx + bw, geo.crop_w)
        if px is not None:
            out[s.key] = round(px, 4)
            if pan and paths_out is not None:
                path = pan_path(samples, s, bx, bx + bw, geo.crop_w, fps)
                if path is not None:
                    paths_out[s.key] = path
    return out


# ====================================================================== 검출 결과 저장 (프로젝트 캐시 폴더)
CACHE_VERSION = 3  # 2: SCRFD 얼굴 + 머리 상자 + 1280 분석, 3: 얼굴 방향(yaw) (예전 검출 결과는 다시 분석)


def load_cache(path: Path) -> dict[int, list[Det]]:
    import json

    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("v") != CACHE_VERSION or d.get("w") != ANALYSIS_WIDTH:
            return {}
        return {
            int(k): [Det(*r[:5], bool(r[5]), float(r[6]) if len(r) > 6 else 0.0) for r in v]
            for k, v in d.get("frames", {}).items()
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def save_cache(path: Path, cache: dict[int, list[Det]]) -> None:
    import json

    data = {
        "v": CACHE_VERSION,
        "w": ANALYSIS_WIDTH,
        "frames": {
            str(k): [[round(d.x1, 1), round(d.y1, 1), round(d.x2, 1), round(d.y2, 1), round(d.weight, 5), int(d.face),
                 round(d.yaw, 3)]
                     for d in v]
            for k, v in cache.items()
        },
    }  # fmt: skip
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)
