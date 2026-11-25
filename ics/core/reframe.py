"""STORY / REELS 9:16 Fill 의 컷별 가로 위치(리프레이밍).

- 컷마다 가로 위치(pos_x) 하나만 둔다. 키프레임(컷 안에서 움직이는 크롭)은 없다. 세로 위치와 확대는 바꾸지 않는다.
- 자동: 컷 안에서 고르게 뽑은 프레임에서 인물(몸)과 얼굴을 찾고, 9:16 창 안에 인물이 평균적으로 가장 많이 보이는
  가로 위치를 고른다. 인물이 나왔다 사라지는 컷도 평균으로 처리되며, 인물이 없는 컷은 위치를 바꾸지 않는다.
- 수동: 미리보기를 좌우로 끌어 지금 컷의 위치만 바꾼다.

이 모듈은 Qt 에 의존하지 않는다. 인물 검출에는 OpenCV DNN 과 함께 배포하는 ONNX 모델 두 개를 쓴다
(NanoDet-Plus 인물 검출 Apache-2.0, YuNet 얼굴 검출 MIT; LICENSES/THIRD_PARTY_NOTICES.md).
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


def pos_for_frame(frame: int, cuts: Sequence, cut_x: dict[int, float], default: float) -> float:
    """frame 이 속한 컷의 가로 위치 (지정 안 됐으면 default)."""
    if not cut_x:
        return default
    for c in cuts:
        if c.start <= frame < c.end:
            return cut_x.get(int(c.start), default)
    return cut_x.get(-1, default)


def crop_steps(
    disp_w: int, disp_h: int, layout: LayoutParams, segs: Sequence[Segment], cut_x: dict[int, float]
) -> list[tuple[int, int]]:
    """렌더링용 [(선택 시작 기준 프레임 오프셋, 표시 좌표 crop_x)]. Fill 이 아니거나 컷별 위치가 없으면 빈 목록."""
    if layout.mode != "fill" or not segs or not cut_x:
        return []
    base = segs[0].start
    steps: list[tuple[int, int]] = []
    for s in segs:
        px = cut_x.get(s.key, layout.pos_x)
        x = compute_geometry(disp_w, disp_h, "916", replace(layout, pos_x=px)).crop_x
        if not steps or steps[-1][1] != x:
            steps.append((s.start - base, x))
    return steps


# ====================================================================== 가로 위치 고르기
@dataclass
class Det:
    x1: float
    y1: float
    x2: float
    y2: float
    weight: float  # 검출 신뢰도 × 크기 (얼굴은 가중)


FACE_WEIGHT = 1.6  # 얼굴이 몸보다 중요하다 (몸이 잘려도 얼굴이 보이는 편이 낫다)
SCORE_TOLERANCE = 0.97  # 최고 점수의 이 비율 안이면 같은 노출로 보고, 그중 인물 무리 가운데에 가장 가까운 위치


def best_window_x(frames: Sequence[Sequence[Det]], lo: float, hi: float, win_w: float, steps: int = 96) -> float | None:
    """창 [x, x+win_w] 의 x 를 [lo, hi-win_w] 에서 고른다: 모든 표본 프레임에 대해 '창 안에 보이는 인물 폭 비율 × 가중치'
    의 합이 가장 큰 위치. 인물이 하나도 없으면 None (위치를 바꾸지 않음). 반환: 0..1 위치 (pos_x)."""
    span = hi - lo - win_w
    dets = [d for fr in frames for d in fr if d.x2 > d.x1]
    if not dets:
        return None
    if span <= 0.5:
        return 0.5
    x1 = np.array([d.x1 for d in dets])
    x2 = np.array([d.x2 for d in dets])
    w = np.array([d.weight for d in dets])
    xs = lo + np.linspace(0.0, span, steps + 1)
    ov = np.clip(np.minimum(x2[None, :], xs[:, None] + win_w) - np.maximum(x1[None, :], xs[:, None]), 0, None)
    cover = ov / (x2 - x1)[None, :]
    score = (cover * w[None, :]).sum(axis=1)
    if score.max() <= 1e-9:
        return None
    good = np.where(score >= score.max() * SCORE_TOLERANCE)[0]
    # 같은 노출이면 창 가운데가 '보이는 인물들의 가중 중심' 에 가장 가까운 위치 (인물을 화면 가운데로)
    best, best_d = int(good[0]), math.inf
    cx = (x1 + x2) / 2
    for i in good:
        cw = cover[i] * w
        if cw.sum() <= 0:
            continue
        center = float((cx * cw).sum() / cw.sum())
        d = abs(xs[i] + win_w / 2 - center)
        if d < best_d:
            best, best_d = int(i), d
    return float((xs[best] - lo) / span)


# ====================================================================== 인물 / 얼굴 검출
def models_dir() -> Path:
    from .. import paths

    return Path(paths.resource_path("assets/models"))


NANODET_FILE = "object_detection_nanodet_2022nov.onnx"
YUNET_FILE = "face_detection_yunet_2023mar.onnx"


class PersonDetector:
    """NanoDet-Plus(COCO 'person') + YuNet(얼굴). 입력은 RGB uint8 HxWx3. 스레드마다 하나씩 만든다 (cv2.dnn 객체는 공유 불가)."""

    SIZE = 416
    STRIDES = (8, 16, 32)
    REG_MAX = 7
    PERSON_TH = 0.35
    FACE_TH = 0.6

    def __init__(self, model_dir: Path | None = None):
        import cv2

        try:  # 백엔드 선택 경고 같은 무해한 로그는 숨긴다
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except Exception:  # noqa: BLE001
            pass
        self.cv2 = cv2
        d = Path(model_dir or models_dir())
        self.net = cv2.dnn.readNet(str(d / NANODET_FILE))
        self.face = cv2.FaceDetectorYN.create(str(d / YUNET_FILE), "", (320, 320), self.FACE_TH, 0.3, 50)
        self.mean = np.array([103.53, 116.28, 123.675], np.float32)
        self.std = np.array([57.375, 57.12, 58.395], np.float32)
        self.anchors = []
        for st in self.STRIDES:
            n = self.SIZE // st
            gx, gy = np.meshgrid(np.arange(n) * st, np.arange(n) * st)
            self.anchors.append(np.column_stack([gx.ravel() + 0.5 * (st - 1), gy.ravel() + 0.5 * (st - 1)]))

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

    def faces(self, rgb: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        h, w = rgb.shape[:2]
        self.face.setInputSize((w, h))
        _, f = self.face.detect(np.ascontiguousarray(rgb[:, :, ::-1]))
        if f is None:
            return []
        return [(float(r[0]), float(r[1]), float(r[0] + r[2]), float(r[1] + r[3]), float(r[14])) for r in f]

    def detect(self, rgb: np.ndarray) -> list[Det]:
        """프레임 크기로 정규화한 가중치: 신뢰도 × sqrt(면적 비율). 큰(가까운) 인물이 주인공이 된다."""
        h, w = rgb.shape[:2]
        area = float(w * h)
        out = [
            Det(x1, y1, x2, y2, c * math.sqrt(max(0.0, (x2 - x1) * (y2 - y1)) / area))
            for x1, y1, x2, y2, c in self.persons(rgb)
        ]
        out += [
            Det(x1, y1, x2, y2, FACE_WEIGHT * c * math.sqrt(max(0.0, (x2 - x1) * (y2 - y1)) / area) * 3.0)
            for x1, y1, x2, y2, c in self.faces(rgb)
        ]
        return out


# ====================================================================== 분석 (원본 디코딩 + 검출)
ANALYSIS_WIDTH = 640  # 검출용 축소 폭 (얼굴이 작아도 잡히고, 디코딩이 빠른 크기)
SAMPLES_PER_SEC = 2.0
MIN_SAMPLES, MAX_SAMPLES = 2, 6  # 컷 하나당 표본 수
MAX_TOTAL_SAMPLES = 240


def sample_frames(seg: Segment, fps: float, budget_scale: float = 1.0) -> list[int]:
    """컷 안에서 고르게 뽑는다. 컷 경계 바로 옆 프레임(디졸브/플래시)은 피한다."""
    n = seg.end - seg.start
    if n <= 0:
        return []
    k = int(round(n / max(1.0, fps) * SAMPLES_PER_SEC * budget_scale))
    k = max(MIN_SAMPLES, min(MAX_SAMPLES, k))
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
) -> dict[int, float]:
    """컷마다 인물이 가장 많이 보이는 가로 위치. 반환 {컷 키: pos_x} (인물을 못 찾은 컷은 빠진다).
    decode(frame, w, h) -> RGB ndarray (표시 좌표 전체 프레임을 w×h 로 축소)."""
    lp = replace(layout, mode="fill")
    dw, dh = info.display_w, info.display_h
    geo = compute_geometry(dw, dh, "916", lp)
    bx, by, bw, bh = source_bounds(dw, dh, lp) or (0, 0, dw, dh)
    if bw - geo.crop_w <= 2:  # 가로로 움직일 여지가 없다 (세로 영상 등)
        return {}
    fps = float(index.fps) if getattr(index, "fps", None) else 24.0
    total = sum(len(sample_frames(s, fps)) for s in segs)
    scale = min(1.0, MAX_TOTAL_SAMPLES / max(1, total))
    plan = [(s, sample_frames(s, fps, scale)) for s in segs]
    jobs = [(i, f) for i, (_s, fs) in enumerate(plan) for f in fs]
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

    per_seg: dict[int, list[list[Det]]] = {i: [] for i in range(len(plan))}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for i, dets in ex.map(work, jobs):
            per_seg[i].append([Det(d.x1 / k, d.y1 / k, d.x2 / k, d.y2 / k, d.weight) for d in dets])
            done += 1
            if progress:
                progress(done / max(1, len(jobs)))
    out: dict[int, float] = {}
    for i, (s, _fs) in enumerate(plan):
        px = best_window_x(per_seg[i], bx, bx + bw, geo.crop_w)
        if px is not None:
            out[s.key] = round(px, 4)
    return out
