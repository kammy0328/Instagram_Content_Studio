"""마스터링 영상 분석 (FFprobe). SDR 전용: HDR 입력은 오류로 분류한다."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Any

from . import ffbin
from .timecode import fps_label, parse_rational, snap_fps

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
SDR_OK_TRANSFERS = {
    "bt709",
    "smpte170m",
    "bt470m",
    "bt470bg",
    "iec61966-2-1",
    "bt2020-10",
    "bt2020-12",
    "gamma22",
    "gamma28",
    "smpte240m",
    "bt1361e",
    "linear",
    "iec61966-2-4",
}
UNKNOWN = {None, "", "unknown", "unspecified", "reserved", "N/A"}
SUPPORTED_EXT = {".mp4", ".mov", ".m4v"}


@dataclass
class Issue:
    level: str  # 'error' | 'warn' | 'info'
    code: str
    message: str  # 한국어 사용자 메시지

    def to_dict(self):
        return asdict(self)


@dataclass
class AudioInfo:
    index: int = 0
    codec: str = ""
    channels: int = 0
    sample_rate: int = 0
    layout: str = ""
    bit_rate: int = 0
    sample_fmt: str = ""


@dataclass
class MediaInfo:
    path: str = ""
    size_bytes: int = 0
    mtime: float = 0.0
    format_name: str = ""
    duration: float = 0.0
    bit_rate: int = 0
    # video
    codec: str = ""
    codec_long: str = ""
    profile: str = ""
    width: int = 0
    height: int = 0
    sar: str = "1/1"
    display_w: int = 0
    display_h: int = 0
    rotation: int = 0
    fps: str = "0/1"  # 프레임 인덱스 전의 평균 FPS (Fraction 문자열)
    r_fps: str = "0/1"
    is_vfr: bool = False
    pix_fmt: str = ""
    bit_depth: int = 8
    chroma: str = ""
    has_alpha: bool = False
    color_range: str = ""  # tv / pc / "" (태그 없음)
    color_space: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    field_order: str = ""
    nb_frames: int = 0
    video_bit_rate: int = 0
    tc_start: str = ""
    start_time: float = 0.0
    video_start_time: float = 0.0
    time_base: str = "1/1"
    hdr: bool = False
    # 색 가정(태그 누락 시)
    eff_matrix: str = "bt709"
    eff_primaries: str = "bt709"
    eff_transfer: str = "bt709"
    eff_range: str = "tv"
    color_assumed: bool = False  # 색공간/원색/전달함수 태그 누락(경고 대상)
    range_assumed: bool = False  # 범위 태그만 없음(정상적인 경우가 많아 안내만)
    audio: list[AudioInfo] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    # ---- helpers
    @property
    def fps_frac(self) -> Fraction:
        return Fraction(self.fps)

    @property
    def sar_frac(self) -> Fraction:
        return Fraction(self.sar)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warn"]

    @property
    def usable(self) -> bool:
        return not self.errors

    def summary_lines(self) -> list[str]:
        f = self.fps_frac
        lines = [
            f"길이: {self.duration:.3f}초",
            f"해상도: {self.width}×{self.height}"
            + (
                f" (SAR {self.sar.replace('/', ':')}, 표시 {self.display_w}×{self.display_h})"
                if self.sar_frac != 1
                else ""
            ),
            f"코덱: {self.codec_long or self.codec}" + (f" / {self.profile}" if self.profile else ""),
            f"비트레이트: {(self.video_bit_rate or self.bit_rate) / 1e6:.1f} Mbps",
            f"FPS: {fps_label(f)}" + (" (VFR 감지)" if self.is_vfr else " (CFR)"),
            f"비트뎁스/픽셀: {self.bit_depth}-bit {self.pix_fmt}",
            f"색공간: {self.color_space or '태그없음'} / 전달함수: {self.color_transfer or '태그없음'}"
            f" / 원색: {self.color_primaries or '태그없음'} / 범위: {self.color_range or '태그없음'}",
        ]
        if self.color_assumed or self.range_assumed:
            lines.append(
                f"적용(가정)값: {self.eff_matrix}/{self.eff_transfer}/{self.eff_primaries}, "
                f"{'Limited' if self.eff_range == 'tv' else 'Full'} range"
            )
        if self.audio:
            a = self.audio[0]
            lines.append(f"오디오: {a.codec} {a.channels}ch {a.sample_rate} Hz")
        else:
            lines.append("오디오: 없음")
        if self.tc_start:
            lines.append(f"시작 타임코드 태그: {self.tc_start}")
        return lines

    # ---- (de)serialization
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MediaInfo":
        d = dict(d)
        d["audio"] = [AudioInfo(**a) for a in d.get("audio", [])]
        d["issues"] = [Issue(**i) for i in d.get("issues", [])]
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def _bit_depth(stream: dict) -> int:
    brs = stream.get("bits_per_raw_sample")
    pix = stream.get("pix_fmt", "") or ""
    m = re.search(r"(\d+)(le|be)?$", pix)
    from_pix = 8
    if m and int(m.group(1)) in (9, 10, 12, 14, 16):
        from_pix = int(m.group(1))
    elif "p10" in pix:
        from_pix = 10
    elif "p12" in pix:
        from_pix = 12
    if brs and str(brs).isdigit() and int(brs) > 0:
        return max(int(brs), from_pix) if from_pix > 8 else int(brs)
    return from_pix


def _chroma(pix: str) -> str:
    for c in ("444", "422", "420", "411", "440"):
        if c in pix:
            return c
    if pix.startswith(("rgb", "bgr", "gbr", "argb", "rgba", "bgra")):
        return "RGB"
    return ""


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tags = stream.get("tags", {}) or {}
    if "rotate" in tags:
        try:
            return int(tags["rotate"]) % 360
        except ValueError:
            pass
    return 0


def analyze(path: str) -> MediaInfo:
    """파일을 분석한다. 파일이 없거나 손상되었으면 error Issue 가 포함된 MediaInfo 를 반환."""
    info = MediaInfo(path=str(path))
    if not os.path.isfile(path):
        info.issues.append(Issue("error", "missing", f"파일을 찾을 수 없습니다: {path}"))
        return info
    st = os.stat(path)
    info.size_bytes, info.mtime = st.st_size, st.st_mtime
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXT:
        info.issues.append(Issue("warn", "ext", f"MP4/MOV 이외의 확장자({ext})입니다. 분석을 계속 시도합니다."))
    try:
        data = ffbin.probe_json(path, "-show_format", "-show_streams")
    except Exception as e:  # noqa: BLE001
        info.issues.append(
            Issue("error", "corrupt", f"파일을 열 수 없습니다. 손상되었거나 지원하지 않는 형식입니다.\n({e})")
        )
        return info

    fmt = data.get("format", {})
    info.format_name = fmt.get("format_name", "")
    info.duration = float(fmt.get("duration") or 0)
    info.bit_rate = int(float(fmt.get("bit_rate") or 0))
    info.start_time = float(fmt.get("start_time") or 0)
    streams = data.get("streams", [])
    vstreams = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition", {}) or {}).get("attached_pic")
    ]
    if not vstreams:
        info.issues.append(Issue("error", "novideo", "영상 스트림이 없습니다. 영상 파일이 맞는지 확인하세요."))
        return info
    v = vstreams[0]
    info.codec = v.get("codec_name", "")
    info.codec_long = v.get("codec_long_name", info.codec)
    info.profile = v.get("profile", "") or ""
    info.width, info.height = int(v.get("width", 0)), int(v.get("height", 0))
    if info.width <= 0 or info.height <= 0:
        info.issues.append(Issue("error", "baddim", "영상 해상도를 읽을 수 없습니다. 파일이 손상되었을 수 있습니다."))
        return info
    sar = parse_rational((v.get("sample_aspect_ratio") or "1:1").replace(":", "/")) or Fraction(1)
    if sar <= 0:
        sar = Fraction(1)
    info.sar = f"{sar.numerator}/{sar.denominator}"
    info.rotation = _rotation(v)
    dw = int(round(info.width * float(sar)))
    dh = info.height
    if info.rotation in (90, 270):
        dw, dh = dh, dw
    info.display_w, info.display_h = dw, dh
    avg = parse_rational(v.get("avg_frame_rate"))
    rfr = parse_rational(v.get("r_frame_rate"))
    fps = avg or rfr or Fraction(0)
    info.fps = str(snap_fps(fps)) if fps else "0/1"
    info.r_fps = str(snap_fps(rfr)) if rfr else info.fps
    if avg and rfr and abs(float(avg) - float(rfr)) / float(rfr) > 0.002:
        info.is_vfr = True
    info.pix_fmt = v.get("pix_fmt", "") or ""
    info.bit_depth = _bit_depth(v)
    info.chroma = _chroma(info.pix_fmt)
    info.has_alpha = bool(re.search(r"(yuva|gbrap|rgba|bgra|argb|abgr|ya)", info.pix_fmt))
    info.color_range = v.get("color_range", "") or ""
    info.color_space = v.get("color_space", "") or ""
    info.color_transfer = v.get("color_transfer", "") or ""
    info.color_primaries = v.get("color_primaries", "") or ""
    info.field_order = v.get("field_order", "") or ""
    info.nb_frames = int(v.get("nb_frames") or 0) if str(v.get("nb_frames", "")).isdigit() else 0
    info.video_bit_rate = int(float(v.get("bit_rate") or 0))
    info.video_start_time = float(v.get("start_time") or 0)
    info.time_base = v.get("time_base", "1/1")
    info.tc_start = (
        (v.get("tags", {}) or {}).get("timecode", "") or (fmt.get("tags", {}) or {}).get("timecode", "") or ""
    )
    if not info.tc_start:
        for s in streams:
            if s.get("codec_type") == "data" and (s.get("tags", {}) or {}).get("timecode"):
                info.tc_start = s["tags"]["timecode"]
                break
    for s in streams:
        if s.get("codec_type") == "audio":
            info.audio.append(
                AudioInfo(
                    index=int(s.get("index", 0)),
                    codec=s.get("codec_name", ""),
                    channels=int(s.get("channels", 0) or 0),
                    sample_rate=int(s.get("sample_rate", 0) or 0),
                    layout=s.get("channel_layout", "") or "",
                    bit_rate=int(float(s.get("bit_rate") or 0)),
                    sample_fmt=s.get("sample_fmt", "") or "",
                )
            )

    _classify_color(info, v)
    _collect_issues(info, v)
    return info


def _classify_color(info: MediaInfo, v: dict) -> None:
    sd_types = {sd.get("side_data_type", "") for sd in (v.get("side_data_list") or [])}
    hdr_side = any(
        ("Mastering display" in t or "Content light level" in t or "DOVI" in t or "HDR Dynamic" in t) for t in sd_types
    )
    info.hdr = (info.color_transfer in HDR_TRANSFERS) or hdr_side
    sd = info.height <= 576 and info.width < 1280  # mpv/VLC 와 동일한 SD/HD 판정
    default_matrix = "bt709"
    default_prim = "bt709"
    default_trc = "bt709"
    if sd:
        default_matrix = default_trc = "smpte170m"
        default_prim = "bt470bg" if info.height == 576 else "smpte170m"
    assumed = False
    info.eff_matrix = info.color_space if info.color_space not in UNKNOWN else default_matrix
    info.eff_primaries = info.color_primaries if info.color_primaries not in UNKNOWN else default_prim
    info.eff_transfer = info.color_transfer if info.color_transfer not in UNKNOWN else default_trc
    if info.color_space in UNKNOWN or info.color_primaries in UNKNOWN or info.color_transfer in UNKNOWN:
        assumed = True
    if info.color_range in ("tv", "pc"):
        info.eff_range = info.color_range
    else:
        # ProRes MOV 등은 범위 태그가 없는 것이 일반적 -> Limited 로 가정(경고 대상 아님)
        info.eff_range = "pc" if info.pix_fmt.startswith("yuvj") or info.chroma == "RGB" else "tv"
        info.range_assumed = True
    info.color_assumed = assumed


def _collect_issues(info: MediaInfo, v: dict) -> None:
    add = info.issues.append
    if info.hdr:
        add(
            Issue(
                "error",
                "hdr",
                "HDR(PQ/HLG/Dolby Vision) 영상은 지원하지 않습니다. SDR(Rec.709) 마스터링 파일을 사용해 주세요.",
            )
        )
    if info.color_primaries == "bt2020" or info.color_space in ("bt2020nc", "bt2020c"):
        if not info.hdr:
            add(
                Issue(
                    "warn",
                    "wide_gamut",
                    "BT.2020 색역 SDR 입니다. 색역 변환 없이 Rec.709 로 태그되어 색이 달라질 수 있으니 "
                    "Rec.709 마스터를 권장합니다.",
                )
            )
    if info.color_transfer and info.color_transfer not in SDR_OK_TRANSFERS | UNKNOWN | HDR_TRANSFERS:
        add(Issue("warn", "transfer", f"알 수 없는 전달함수({info.color_transfer})입니다. SDR 로 가정합니다."))
    if info.color_assumed and not info.hdr:
        add(
            Issue(
                "warn",
                "color_assumed",
                "색상 메타데이터가 일부 누락되어 가정값을 사용합니다 "
                f"({info.eff_matrix}, {'Limited' if info.eff_range == 'tv' else 'Full'} range). "
                "결과 색이 원본과 다르면 마스터링 파일의 색상 태그를 확인하세요.",
            )
        )
    if info.is_vfr:
        add(
            Issue(
                "warn",
                "vfr",
                "가변 프레임레이트(VFR) 영상입니다. 프레임 인덱스를 타임스탬프 기준으로 정밀 구성하고 "
                "출력은 CFR 로 변환합니다.",
            )
        )
    if info.field_order in ("tt", "bb", "tb", "bt"):
        add(Issue("warn", "interlaced", "인터레이스 영상입니다. 출력 시 디인터레이스(bwdif)를 적용합니다."))
    if info.rotation:
        add(Issue("info", "rotation", f"회전 메타데이터({info.rotation}°)가 있어 표시 방향 기준으로 처리합니다."))
    if info.sar_frac != 1:
        add(
            Issue(
                "info",
                "sar",
                f"픽셀 종횡비(SAR {info.sar.replace('/', ':')})가 1:1 이 아니어서 "
                f"정사각 픽셀 {info.display_w}×{info.display_h} 로 환산합니다.",
            )
        )
    if not info.audio:
        add(Issue("warn", "noaudio", "오디오 스트림이 없습니다. 영상만 출력됩니다."))
    if info.has_alpha:
        add(Issue("info", "alpha", "알파 채널이 있는 파일입니다. 알파는 무시하고 컬러 채널만 사용합니다."))
    if info.fps_frac <= 0:
        add(Issue("error", "nofps", "프레임레이트를 확인할 수 없습니다."))
    if info.codec in ("", "none"):
        add(Issue("error", "codec", "지원되지 않는 코덱입니다."))
    if info.duration <= 0:
        add(Issue("warn", "noduration", "길이 정보를 읽을 수 없습니다. 프레임 인덱스로 보정합니다."))
