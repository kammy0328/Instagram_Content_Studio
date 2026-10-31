"""원본에서 정확한 프레임을 디코딩. 색 변환 행렬/범위를 항상 명시해 태그 누락 시에도 일관된 색을 보장."""

from __future__ import annotations

import numpy as np

from . import ffbin
from .frameindex import FrameIndex
from .media import MediaInfo

SCALE_FLAGS = "lanczos+accurate_rnd+full_chroma_int+full_chroma_inp"

_MATRIX_MAP = {
    "bt709": "bt709",
    "smpte170m": "smpte170m",
    "bt470bg": "bt470",
    "fcc": "fcc",
    "smpte240m": "smpte240m",
    "bt2020nc": "bt2020",
    "bt2020c": "bt2020",
    "rgb": None,
}


def in_color_opts(info: MediaInfo) -> str:
    """scale 필터의 입력 색 해석 옵션 (YUV 입력에만 의미)."""
    if info.chroma == "RGB":
        return "in_range=full"
    m = _MATRIX_MAP.get(info.eff_matrix, "bt709") or "bt709"
    r = "full" if info.eff_range == "pc" else "limited"
    return f"in_color_matrix={m}:in_range={r}"


def pre_filters(info: MediaInfo) -> list[str]:
    """디인터레이스 등 스케일 전 필터."""
    f: list[str] = []
    if info.field_order in ("tt", "bb", "tb", "bt"):
        f.append("bwdif=mode=send_frame:parity=auto:deint=all")
    return f


def decode_frame_rgb(
    info: MediaInfo,
    index: FrameIndex,
    n: int,
    width: int | None = None,
    height: int | None = None,
    bit16: bool = False,
    source_path: str | None = None,
    color_opts: str | None = None,
    pre: bool = True,
) -> tuple[np.ndarray, int, int]:
    """n 번째 프레임을 RGB 로 디코딩. width/height 지정 시 해당 크기로 리사이즈(비율 유지는 호출자 책임).
    기본 크기 = 표시(SAR 반영) 크기. 반환: (ndarray HxWx3, w, h)."""
    n = index.clamp(n)
    w = width or info.display_w
    h = height or info.display_h
    pix = "rgb48le" if bit16 else "rgb24"
    vf = ",".join(
        (pre_filters(info) if pre else [])
        + [
            f"scale={w}:{h}:flags={SCALE_FLAGS}:{color_opts or in_color_opts(info)}:out_range=full:sws_dither=ed",
            f"format={pix}",
        ]
    )
    src = source_path or info.path
    cmd = [
        ffbin.ffmpeg(),
        "-v",
        "error",
        "-nostdin",
        "-ss",
        f"{index.seek_time(n):.6f}",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        vf,
        "-f",
        "rawvideo",
        "-pix_fmt",
        pix,
        "-",
    ]
    p = ffbin.run(cmd)
    dt = np.uint16 if bit16 else np.uint8
    raw = np.frombuffer(p.stdout, dt)
    need = w * h * 3
    if raw.size < need:
        raise RuntimeError(f"프레임 {n} 디코딩 실패: " + p.stderr.decode("utf-8", "replace").strip()[:300])
    return raw[:need].reshape(h, w, 3), w, h
