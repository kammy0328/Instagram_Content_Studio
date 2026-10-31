"""출력용 ffmpeg 필터/인코딩 파라미터 자동 결정. 사용자 인코딩 설정은 노출하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from ..core.decode import SCALE_FLAGS, in_color_opts, pre_filters
from ..core.layout import Geometry
from ..core.media import MediaInfo
from ..core.timecode import STANDARD_FPS, snap_fps


def output_fps(info: MediaInfo, vfr_avg: Fraction | None = None) -> Fraction:
    """원본 FPS 보존을 기본으로 하되 VFR 은 가장 가까운 표준 CFR, 60fps 초과는 절반."""
    f = snap_fps(vfr_avg if (info.is_vfr and vfr_avg) else info.fps_frac)
    if info.is_vfr:
        f = min(STANDARD_FPS.values(), key=lambda s: abs(float(s) - float(f)))
    while f > 60:
        f = f / 2
    if f < 23:
        f = Fraction(24) if f > 12 else f
    return f


def video_filter(info: MediaInfo, geo: Geometry, fps_out: Fraction | None, pix_fmt: str = "yuv420p") -> str:
    """원본 디코딩 프레임 -> (디인터레이스) -> [크롭] -> 스케일(+색변환, 디더) -> pad(검정) -> setsar=1."""
    parts: list[str] = pre_filters(info)
    scale_opts = f"flags={SCALE_FLAGS}:{in_color_opts(info)}:out_color_matrix=bt709:out_range=tv:" f"sws_dither=ed"
    if geo.mode == "fill":
        # 크롭 좌표는 표시(SAR 반영) 좌표계 -> 먼저 표시 크기로 정사각 픽셀화 후 크롭하면 이중 리샘플링이 되므로,
        # 코딩 좌표로 환산해 크롭 후 한 번에 스케일한다.
        gw = info.width if info.rotation not in (90, 270) else info.height
        sx = gw / info.display_w
        cw = max(2, int(round(geo.crop_w * sx)))
        cx = int(round(geo.crop_x * sx))
        if cx + cw > info.width:
            cw = info.width - cx
        parts.append(f"crop={cw}:{geo.crop_h}:{cx}:{geo.crop_y}")
        parts.append(f"scale={geo.canvas_w}:{geo.canvas_h}:{scale_opts}")
        parts.append(f"format={pix_fmt}")
    else:
        parts.append(f"scale={geo.content_w}:{geo.content_h}:{scale_opts}")
        parts.append(f"format={pix_fmt}")
        if (geo.content_w, geo.content_h) != (geo.canvas_w, geo.canvas_h):
            parts.append(f"pad={geo.canvas_w}:{geo.canvas_h}:{geo.pad_x}:{geo.pad_y}:color=black")
    parts.append("setsar=1")
    if fps_out is not None:
        parts.append(f"fps=fps={fps_out.numerator}/{fps_out.denominator}:round=near")
    return ",".join(parts)


@dataclass
class EncodeParams:
    args: list[str]
    crf: int
    description: str


def x264_params(geo: Geometry, fps: Fraction) -> EncodeParams:
    """고화질 우선 자동 파라미터. (사용자 UI 에는 결과 파일 정보만 표시)"""
    px = geo.canvas_w * geo.canvas_h
    crf = 14 if px <= 1080 * 1350 else 15
    level = "4.2" if (px * float(fps)) > 1920 * 1080 * 31 else "4.1"
    gop = max(12, int(round(float(fps) * 2)))
    maxrate = "45M" if px > 1080 * 1350 else "40M"
    args = [
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        str(crf),
        "-profile:v",
        "high",
        "-level:v",
        level,
        "-pix_fmt",
        "yuv420p",
        "-maxrate",
        maxrate,
        "-bufsize",
        "90M",
        "-g",
        str(gop),
        "-bf",
        "3",
        "-refs",
        "4",
        "-x264-params",
        "aq-mode=3:aq-strength=0.9:deblock=-1,-1:psy-rd=1.0,0.15",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-movflags",
        "+faststart",
    ]
    return EncodeParams(args, crf, f"H.264 High@{level} CRF{crf} slow · yuv420p · BT.709 Limited")


def audio_filter_and_args(info: MediaInfo, start_trim: float, duration: float) -> tuple[str | None, list[str]]:
    """오디오: 샘플 정확 트림 + 볼륨 변경 없음. 채널 3개 이상만 스테레오 다운믹스(클리핑 방지)."""
    if not info.has_audio:
        return None, ["-an"]
    a = info.audio[0]
    af = [f"atrim=start={start_trim:.9f}:duration={duration:.9f}", "asetpts=PTS-STARTPTS"]
    ch = a.channels if a.channels in (1, 2) else 2
    sr = a.sample_rate if a.sample_rate in (44100, 48000) else 48000
    if a.channels > 2:
        af.append("aresample=rematrix_maxval=1.0")
    if a.sample_rate != sr:
        af.append(f"aresample={sr}")
    br = "256k" if ch == 2 else "160k"
    return ",".join(af), ["-c:a", "aac", "-b:a", br, "-ac", str(ch), "-ar", str(sr)]


def still_filter(info: MediaInfo, geo: Geometry, pix_fmt: str = "rgb24") -> str:
    """스틸용 필터: 원본 디코딩 프레임을 RGB(Full)로 변환하며 한 번에 리사이즈. 여백은 순수 #000000."""
    parts: list[str] = pre_filters(info)
    scale_opts = f"flags={SCALE_FLAGS}:{in_color_opts(info)}:out_range=full:sws_dither=ed"
    if geo.mode == "fill":
        gw = info.width if info.rotation not in (90, 270) else info.height
        sx = gw / info.display_w
        cw = max(2, int(round(geo.crop_w * sx)))
        cx = int(round(geo.crop_x * sx))
        if cx + cw > gw:
            cw = gw - cx
        parts.append(f"crop={cw}:{geo.crop_h}:{cx}:{geo.crop_y}")
        parts.append(f"scale={geo.canvas_w}:{geo.canvas_h}:{scale_opts}")
        parts.append(f"format={pix_fmt}")
    else:
        parts.append(f"scale={geo.content_w}:{geo.content_h}:{scale_opts}")
        parts.append(f"format={pix_fmt}")
        if (geo.content_w, geo.content_h) != (geo.canvas_w, geo.canvas_h):
            parts.append(f"pad={geo.canvas_w}:{geo.canvas_h}:{geo.pad_x}:{geo.pad_y}:color=black")
    parts.append("setsar=1")
    return ",".join(parts)
