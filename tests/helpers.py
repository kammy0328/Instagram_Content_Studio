"""테스트용 미디어 생성/판독 도구: 각 프레임에 24비트 프레임번호 코드를 새겨 정확도를 검증한다."""

from __future__ import annotations

import subprocess
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np

from ics.core import ffbin

NBITS = 24


def make_frame(n: int, w: int, h: int) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    # 배경 그라디언트
    img[..., 0] = np.linspace(40, 200, w, dtype=np.uint8)[None, :]
    img[..., 1] = np.linspace(60, 160, h, dtype=np.uint8)[:, None]
    img[..., 2] = 90
    bw = w // NBITS
    bh = max(8, h // 12)
    for b in range(NBITS):
        bit = (n >> (NBITS - 1 - b)) & 1
        img[0:bh, b * bw : (b + 1) * bw, :] = 255 if bit else 0
    # 4px 흰 테두리 (Fit 시 크롭 여부 검사)
    img[:, :4] = 255
    img[:, -4:] = 255
    img[-4:, :] = 255
    return img


def decode_code(
    img: np.ndarray,
    x0: float = 0,
    y0: float = 0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    src_w: int = 960,
    src_h: int = 540,
) -> int:
    """출력 이미지 img 안에서 원본 좌표계(src_w x src_h)의 비트 블록을 읽는다."""
    bw = src_w // NBITS
    bh = max(8, src_h // 12)
    val = 0
    for b in range(NBITS):
        cx = x0 + (b * bw + bw / 2) * scale_x
        cy = y0 + (bh / 2) * scale_y
        patch = img[int(cy) - 1 : int(cy) + 2, int(cx) - 1 : int(cx) + 2, :].mean()
        val = (val << 1) | (1 if patch > 128 else 0)
    return val


def make_audio(path: Path, seconds: float, sr: int = 48000, ch: int = 2) -> None:
    n = int(seconds * sr)
    t = np.arange(n) / sr
    sig = 0.15 * np.sin(2 * np.pi * 220 * t)
    for k in range(int(seconds) + 1):
        s = int(k * sr)
        e = min(n, s + int(0.010 * sr))
        sig[s:e] = 0.9 * np.sin(2 * np.pi * 1000 * t[s:e])
    pcm = (sig * 32767).astype("<i2")
    data = np.repeat(pcm[:, None], ch, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def make_video(
    path: str | Path,
    w: int = 960,
    h: int = 540,
    fps: Fraction = Fraction(24000, 1001),
    frames: int = 240,
    vcodec: str = "prores",
    pix_fmt: str = "yuv422p10le",
    audio: bool = True,
    vfr_drop: bool = False,
    tags: bool = True,
    full_range: bool = False,
    sar: str | None = None,
    extra: list[str] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wav = path.with_suffix(".wav")
    if audio:
        make_audio(wav, frames / float(fps) + 0.5)
    rng = "pc" if full_range else "tv"
    vf = [f"scale=out_color_matrix=bt709:out_range={rng}:flags=accurate_rnd+full_chroma_int", f"format={pix_fmt}"]
    if vfr_drop:
        vf.insert(0, "select='not(eq(mod(n\\,5)\\,3))'")
    cmd = [
        ffbin.ffmpeg(),
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-framerate",
        str(fps),
        "-i",
        "-",
    ]
    if audio:
        cmd += ["-i", str(wav)]
    cmd += ["-vf", ",".join(vf)]
    if vfr_drop:
        cmd += ["-fps_mode", "vfr"]
    if vcodec == "prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", "3"]
    elif vcodec == "prores4444":
        cmd += ["-c:v", "prores_ks", "-profile:v", "4"]
    elif vcodec == "h264":
        cmd += ["-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-bf", "2", "-g", "48"]
    elif vcodec == "h265":
        cmd += ["-c:v", "libx265", "-crf", "14", "-preset", "veryfast"]
    cmd += ["-pix_fmt", pix_fmt]
    if tags:
        cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", rng]
    if sar:
        cmd += ["-vf", ",".join(vf) + f",setsar={sar}"]
        # 마지막 -vf 가 우선
    if audio:
        cmd += ["-c:a", "pcm_s16le" if str(path).endswith(".mov") else "aac", "-shortest"]
    cmd += (extra or []) + [str(path)]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    for n in range(frames):
        pr.stdin.write(make_frame(n, w, h).tobytes())
    pr.stdin.close()
    err = pr.stderr.read().decode()
    if pr.wait() != 0:
        raise RuntimeError(err)
    if audio and wav.exists():
        wav.unlink()
    return path


def read_frame_rgb(
    path: str | Path,
    t: float | None = None,
    n: int | None = None,
    fps: float = 24.0,
    w: int | None = None,
    h: int | None = None,
) -> np.ndarray:
    """영상에서 시각 t 직후 첫 프레임을 RGB 로 읽는다 (n 지정 시 n 번째 출력 프레임)."""
    pj = ffbin.probe_json(str(path), "-select_streams", "v:0", "-show_entries", "stream=width,height")
    s = pj["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    cmd = [ffbin.ffmpeg(), "-v", "error"]
    if t is not None:
        cmd += ["-ss", f"{t:.6f}"]
    cmd += ["-i", str(path)]
    if n is not None:
        cmd += ["-vf", f"select=eq(n\\,{n})", "-fps_mode", "passthrough"]
    cmd += ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = ffbin.run(cmd)
    return np.frombuffer(p.stdout, np.uint8).reshape(h, w, 3)


def all_frame_codes(path: str | Path, src_w=960, src_h=540, x0=0, y0=0, sx=1.0, sy=1.0) -> list[int]:
    pj = ffbin.probe_json(str(path), "-select_streams", "v:0", "-show_entries", "stream=width,height")
    s = pj["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    p = ffbin.run(
        [
            ffbin.ffmpeg(),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-fps_mode",
            "passthrough",
            "-",
        ]
    )
    arr = np.frombuffer(p.stdout, np.uint8)
    fsz = w * h * 3
    out = []
    for i in range(len(arr) // fsz):
        img = arr[i * fsz : (i + 1) * fsz].reshape(h, w, 3)
        out.append(decode_code(img, x0, y0, sx, sy, src_w, src_h))
    return out
