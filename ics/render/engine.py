"""실제 인코딩/스틸 생성 엔진. 항상 원본 파일에서 직접 디코딩한다(프록시 사용 금지)."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np

from ..core import ffbin
from ..core.decode import SCALE_FLAGS, in_color_opts, pre_filters
from ..core.frameindex import FrameIndex
from ..core.layout import Geometry
from ..core.media import MediaInfo
from . import filters

PROXY_MARKER = "proxy"


class RenderCancelled(Exception):
    pass


class RenderError(Exception):
    pass


@dataclass
class VideoSpec:
    start: int  # 포함 시작 프레임
    end: int  # 미포함 끝 프레임 (exclusive)
    geo: Geometry
    fps_out: Fraction | None = None
    overlay_png: str | None = None  # 캔버스 크기 투명 PNG (STORY 로고/제목). 여백(배경) 위에만 그려져 있다
    bg_dim: int | None = None  # 값이 있으면 여백을 흐린 영상 배경으로 채우고 이만큼(%) 어둡게 한다


def _assert_original(info: MediaInfo, src: str) -> None:
    """최종 출력은 반드시 마스터링 원본을 사용 (프록시 경로 차단)."""
    if os.path.abspath(src) != os.path.abspath(info.path):
        raise RenderError("최종 렌더링에는 원본 파일만 사용할 수 있습니다 (프록시 사용 금지).")


def clip_times(index: FrameIndex, start: int, end: int) -> tuple[float, float, float, int]:
    """(seek 시각, 오디오 트림 시작 오프셋, 길이(초), 원본 프레임 수)"""
    start = index.clamp(start)
    end = max(start + 1, min(int(end), index.count))
    t_a = index.time_of(start)
    t_b = index.time_of(end)
    seek = Fraction(index.seek_time(start)).limit_denominator(10_000_000)
    return float(seek), float(t_a - seek), float(t_b - t_a), end - start


def build_video_cmd(
    info: MediaInfo, index: FrameIndex, spec: VideoSpec, out_path: str, src: str | None = None
) -> tuple[list[str], dict]:
    src = src or info.path
    _assert_original(info, src)
    seek, a_off, dur, nsrc = clip_times(index, spec.start, spec.end)
    fps_out = spec.fps_out or filters.output_fps(info, index.fps)
    n_out = max(1, int(round(dur * float(fps_out)))) if not index.is_cfr else nsrc
    if index.is_cfr and fps_out != index.fps:
        n_out = max(1, int(round(dur * float(fps_out))))
    use_ov = bool(spec.overlay_png)
    if use_ov and not os.path.isfile(spec.overlay_png):
        raise RenderError(f"오버레이 이미지를 찾을 수 없습니다: {spec.overlay_png}")
    use_bg = filters.has_bg(spec.geo, spec.bg_dim)
    use_fc = use_ov or use_bg
    vf = filters.video_filter(
        info,
        spec.geo,
        fps_out if (not index.is_cfr or fps_out != index.fps) else None,
        overlay=use_ov,
        bg_dim=spec.bg_dim,
    )
    enc = filters.x264_params(spec.geo, fps_out)
    af, aargs = filters.audio_filter_and_args(info, a_off, dur)
    cmd = [
        ffbin.ffmpeg(),
        "-y",
        "-nostdin",
        "-v",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        "-ss",
        f"{seek:.6f}",
        "-i",
        src,
    ]
    if use_fc:
        if use_ov:
            cmd += ["-i", spec.overlay_png]
        cmd += ["-filter_complex", vf.replace("[0:v]", "[0:v]setpts=PTS-STARTPTS,", 1)]
        cmd += ["-map", "[vout]"]
    else:
        cmd += ["-map", "0:v:0"]
    if af:
        cmd += ["-map", "0:a:0"]
    if not use_fc:
        # setpts 로 첫 프레임을 0 으로 정렬한 뒤 필터 적용
        cmd += ["-vf", "setpts=PTS-STARTPTS," + vf]
    if af:
        cmd += ["-af", af]
    cmd += (
        enc.args
        + aargs
        + [
            "-frames:v",
            str(n_out),
            "-r",
            f"{fps_out.numerator}/{fps_out.denominator}",
            "-fps_mode",
            "cfr",
            "-f",
            "mp4",
            out_path,
        ]
    )
    meta = {
        "frames": n_out,
        "duration": dur,
        "fps": str(fps_out),
        "encoder": enc.description,
        "canvas": f"{spec.geo.canvas_w}x{spec.geo.canvas_h}",
        "seek": seek,
        "audio_offset": a_off,
        "overlay": bool(spec.overlay_png),
        "bg_blur": use_bg,
    }
    return cmd, meta


def run_with_progress(
    cmd: list[str],
    total_us: float,
    on_progress: Callable[[float], None] | None,
    cancel: threading.Event | None,
    proc_holder: list | None = None,
) -> tuple[int, str]:
    proc = ffbin.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    if proc_holder is not None:
        proc_holder.append(proc)
    err_chunks: list[bytes] = []

    def _drain():
        for chunk in iter(lambda: proc.stderr.read(4096), b""):
            err_chunks.append(chunk)
            if len(err_chunks) > 64:
                del err_chunks[:32]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        for raw in iter(proc.stdout.readline, b""):
            if cancel is not None and cancel.is_set():
                proc.kill()
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = float(line.split("=", 1)[1])
                except ValueError:
                    continue
                if on_progress and total_us > 0:
                    on_progress(min(0.999, max(0.0, us / total_us)))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        t.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise RenderCancelled()
    return proc.returncode, b"".join(err_chunks).decode("utf-8", "replace")[-1500:]


def render_video(
    info: MediaInfo,
    index: FrameIndex,
    spec: VideoSpec,
    final_path: str,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    """임시(.part) 파일로 인코딩 -> 검증 -> 원자적 이름 변경. 불완전 파일을 남기지 않는다."""
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.name + ".part")
    cmd, meta = build_video_cmd(info, index, spec, str(part))
    try:
        rc, err = run_with_progress(cmd, meta["duration"] * 1e6, on_progress, cancel)
        if rc != 0:
            raise RenderError("ffmpeg 인코딩 실패:\n" + err.strip())
        result = verify_video(str(part), meta["frames"], meta["duration"], info.has_audio, spec.geo)
        if cancel is not None and cancel.is_set():
            raise RenderCancelled()
        os.replace(part, final)
        result.update(meta)
        result["path"] = str(final)
        result["command"] = " ".join(cmd)
        return result
    except BaseException:
        try:
            if part.exists():
                part.unlink()
        except OSError:
            pass
        raise


def verify_video(path: str, expect_frames: int, expect_dur: float, expect_audio: bool, geo: Geometry) -> dict:
    """결과 파일 재생 가능 여부와 규격 확인. 문제가 있으면 RenderError."""
    try:
        d = ffbin.probe_json(path, "-count_packets", "-show_format", "-show_streams")
    except Exception as e:  # noqa: BLE001
        raise RenderError(f"결과 파일을 열 수 없습니다: {e}") from e
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise RenderError("결과 파일에 영상 스트림이 없습니다.")
    problems = []
    if (int(v["width"]), int(v["height"])) != (geo.canvas_w, geo.canvas_h):
        problems.append(f"해상도 불일치 {v['width']}x{v['height']}")
    nb = int(v.get("nb_read_packets", 0) or 0)
    if nb != expect_frames:
        problems.append(f"프레임 수 불일치 {nb} / 기대 {expect_frames}")
    if expect_audio and not a:
        problems.append("오디오 스트림 누락")
    vdur = float(v.get("duration") or d["format"].get("duration") or 0)
    if abs(vdur - expect_dur) > max(0.2, expect_dur * 0.01):
        problems.append(f"길이 불일치 {vdur:.3f}s / 기대 {expect_dur:.3f}s")
    # 전체 디코딩 가능 여부
    p = ffbin.run([ffbin.ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-f", "null", "-"])
    if p.returncode != 0 or p.stderr.strip():
        problems.append("디코딩 오류: " + p.stderr.decode("utf-8", "replace").strip()[:200])
    if problems:
        raise RenderError("결과 파일 검증 실패: " + "; ".join(problems))
    res = {
        "size_bytes": os.path.getsize(path),
        "codec": v.get("codec_name"),
        "profile": v.get("profile"),
        "pix_fmt": v.get("pix_fmt"),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": v.get("avg_frame_rate"),
        "frames": nb,
        "duration": vdur,
        "bit_rate": int(float(d["format"].get("bit_rate") or 0)),
        "audio": (
            None
            if not a
            else {"codec": a.get("codec_name"), "channels": a.get("channels"), "sample_rate": a.get("sample_rate")}
        ),
    }
    if a:
        res["audio_check"] = audio_check(path)
    return res


def audio_check(path: str) -> dict:
    """클리핑/피크 점검 (astats). 볼륨은 절대 변경하지 않고 보고만 한다."""
    p = ffbin.run(
        [
            ffbin.ffmpeg(),
            "-v",
            "info",
            "-nostdin",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-af",
            "astats=metadata=0:reset=0",
            "-f",
            "null",
            "-",
        ]
    )
    txt = p.stderr.decode("utf-8", "replace")
    peak = None
    section = txt.split("Overall", 1)[-1]
    for ln in section.splitlines():
        ln = ln.strip()
        if "Peak level dB:" in ln:
            try:
                peak = float(ln.split(":")[-1].strip())
            except ValueError:
                pass
        if "Number of samples clipped" in ln or "Peak count" in ln:
            pass
    out = {"peak_db": peak, "clipping_risk": peak is not None and peak >= -0.1}
    return out


# ============================================================================== 스틸
_ICC_CACHE: bytes | None = None


def srgb_icc() -> bytes | None:
    global _ICC_CACHE
    if _ICC_CACHE is None:
        try:
            from PIL import ImageCms

            _ICC_CACHE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        except Exception:  # noqa: BLE001
            _ICC_CACHE = b""
    return _ICC_CACHE or None


def decode_still_rgb(info: MediaInfo, index: FrameIndex, n: int, geo: Geometry, src: str | None = None) -> np.ndarray:
    src = src or info.path
    _assert_original(info, src)
    n = index.clamp(n)
    vf = filters.still_filter(info, geo, "rgb24")
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
        "rgb24",
        "-",
    ]
    p = ffbin.run(cmd)
    need = geo.canvas_w * geo.canvas_h * 3
    if len(p.stdout) < need:
        raise RenderError(f"프레임 {n} 디코딩 실패: " + p.stderr.decode("utf-8", "replace").strip()[:300])
    return np.frombuffer(p.stdout[:need], np.uint8).reshape(geo.canvas_h, geo.canvas_w, 3)


def render_jpeg_still(
    info: MediaInfo, index: FrameIndex, n: int, geo: Geometry, final_path: str, quality: int = 97
) -> dict:
    from PIL import Image

    arr = decode_still_rgb(info, index, n, geo)
    img = Image.fromarray(arr, "RGB")
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.name + ".part")
    kw = dict(format="JPEG", quality=quality, subsampling=0, optimize=True, dpi=(72, 72))
    icc = srgb_icc()
    if icc:
        kw["icc_profile"] = icc
    try:
        img.save(part, **kw)
        # 검증
        with Image.open(part) as chk:
            chk.verify()
        os.replace(part, final)
    except BaseException:
        if part.exists():
            part.unlink()
        raise
    return {
        "path": str(final),
        "size_bytes": os.path.getsize(final),
        "width": geo.canvas_w,
        "height": geo.canvas_h,
        "frame": n,
        "icc": bool(icc),
    }


def original_still_format(info: MediaInfo) -> tuple[str, str]:
    """(확장자, pix_fmt). 8bit 는 PNG, 고비트뎁스는 16-bit TIFF."""
    if info.bit_depth > 8:
        return "tiff", "rgb48le"
    return "png", "rgb24"


def render_original_still(info: MediaInfo, index: FrameIndex, n: int, final_path: str) -> dict:
    """원본 해상도 보관용: 업/다운스케일 없음, 크롭/여백 없음. (SAR≠1 인 경우에만 정사각 픽셀 환산)"""
    ext, pix = original_still_format(info)
    n = index.clamp(n)
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.name + ".part")
    w, h = info.display_w, info.display_h
    needs_resize = (w, h) != (info.width, info.height)
    scale = (
        f"scale={w}:{h}:" if needs_resize else "scale=iw:ih:"
    ) + f"flags={SCALE_FLAGS}:{in_color_opts(info)}:out_range=full:sws_dither=ed"
    vf = ",".join(pre_filters(info) + [scale, f"format={pix}", "setsar=1"])
    cmd = [
        ffbin.ffmpeg(),
        "-y",
        "-v",
        "error",
        "-nostdin",
        "-ss",
        f"{index.seek_time(n):.6f}",
        "-i",
        info.path,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        vf,
    ]
    if ext == "png":
        cmd += ["-c:v", "png", "-compression_level", "6", "-pix_fmt", pix, "-f", "image2", "-update", "1"]
    else:
        cmd += ["-c:v", "tiff", "-compression_algo", "deflate", "-pix_fmt", pix, "-f", "image2", "-update", "1"]
    cmd += [str(part)]
    p = ffbin.run(cmd)
    try:
        if p.returncode != 0 or not part.exists() or part.stat().st_size == 0:
            raise RenderError("원본 해상도 스틸 생성 실패: " + p.stderr.decode("utf-8", "replace").strip()[:300])
        os.replace(part, final)
    except BaseException:
        if part.exists():
            part.unlink()
        raise
    return {
        "path": str(final),
        "size_bytes": os.path.getsize(final),
        "format": ext.upper(),
        "bit_depth": 16 if ext == "tiff" else 8,
        "width": w,
        "height": h,
        "frame": n,
    }
