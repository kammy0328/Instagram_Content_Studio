"""백그라운드 서비스: 프록시 생성, 프레임 디코딩, 타임라인 썸네일, 재생 엔진, 오디오 재생."""

from __future__ import annotations

import collections
import os
import queue
import struct
import threading
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

from ..core import ffbin
from ..core.decode import decode_frame_rgb, in_color_opts, pre_filters
from ..core.frameindex import FrameIndex, _cache_key, build_frame_index
from ..core.media import MediaInfo, analyze

PROXY_COLOR = "in_color_matrix=bt709:in_range=full"
PREVIEW_MAX_W = 960


def preview_size(info: MediaInfo, max_w: int = PREVIEW_MAX_W) -> tuple[int, int]:
    w = min(info.display_w, max_w)
    h = int(round(w * info.display_h / info.display_w))
    return max(2, w - w % 2), max(2, h - h % 2)


def np_to_qimage(arr: np.ndarray) -> QImage:
    h, w, _ = arr.shape
    img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return img.copy()


# ======================================================================= 프록시
class ProxyBuilder(QObject):
    """고해상도 재생용 프록시(MJPEG all-intra, 프레임 번호 1:1)와 오디오 WAV 생성. 최종 렌더링에는 사용하지 않는다."""

    progress = Signal(float)
    finished = Signal(bool, str)

    def __init__(self, info: MediaInfo, cache_dir: Path):
        super().__init__()
        self.info, self.cache_dir = info, Path(cache_dir)
        key = _cache_key(info)
        self.video_path = self.cache_dir / f"proxy_{key}.mkv"
        self.audio_path = self.cache_dir / f"proxy_{key}.wav"
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.index: FrameIndex | None = None

    def ready(self) -> bool:
        return self.video_path.is_file() and (self.audio_path.is_file() or not self.info.has_audio)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ics-proxy", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        info = self.info
        try:
            if not self.video_path.is_file():
                w, h = preview_size(info, 1280)
                vf = ",".join(
                    pre_filters(info)
                    + [
                        f"scale={w}:{h}:flags=bilinear+accurate_rnd+full_chroma_int:{in_color_opts(info)}:"
                        f"out_color_matrix=bt709:out_range=full",
                        "format=yuvj420p",
                    ]
                )
                part = self.video_path.with_name(self.video_path.name + ".part")
                cmd = [
                    ffbin.ffmpeg(),
                    "-y",
                    "-nostdin",
                    "-v",
                    "error",
                    "-nostats",
                    "-progress",
                    "pipe:1",
                    "-i",
                    info.path,
                    "-map",
                    "0:v:0",
                    "-an",
                    "-vf",
                    vf,
                    "-c:v",
                    "mjpeg",
                    "-q:v",
                    "3",
                    "-fps_mode",
                    "passthrough",
                    "-f",
                    "matroska",
                    str(part),
                ]
                from ..render.engine import run_with_progress

                rc, err = run_with_progress(
                    cmd, max(0.1, info.duration) * 1e6, lambda p: self.progress.emit(p * 0.9), self._cancel
                )
                if rc != 0:
                    raise RuntimeError(err or "프록시 생성 실패")
                os.replace(part, self.video_path)
            if info.has_audio and not self.audio_path.is_file():
                part = self.audio_path.with_name(self.audio_path.name + ".part")
                p = ffbin.run(
                    [
                        ffbin.ffmpeg(),
                        "-y",
                        "-nostdin",
                        "-v",
                        "error",
                        "-i",
                        info.path,
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-ac",
                        "2",
                        "-ar",
                        "48000",
                        "-c:a",
                        "pcm_s16le",
                        "-f",
                        "wav",
                        str(part),
                    ]
                )
                if p.returncode == 0:
                    os.replace(part, self.audio_path)
            pinfo = analyze(str(self.video_path))
            self.index = build_frame_index(pinfo, self.cache_dir)
            self.progress.emit(1.0)
            self.finished.emit(True, "")
        except Exception as e:  # noqa: BLE001
            from ..render.engine import RenderCancelled

            self.finished.emit(False, "취소됨" if isinstance(e, RenderCancelled) else str(e))


# ======================================================================= 디코더
class Decoder:
    """프록시가 준비되면 프록시, 아니면 원본에서 프리뷰용 프레임을 디코딩(색 변환 명시)."""

    def __init__(self, info: MediaInfo, index: FrameIndex):
        self.info, self.index = info, index
        self.proxy_path: str | None = None
        self.proxy_index: FrameIndex | None = None
        self.size = preview_size(info)

    def set_proxy(self, path: str, index: FrameIndex) -> None:
        if index.count == self.index.count:  # 프레임 수가 같을 때만 신뢰 (1:1 대응)
            self.proxy_index, self.proxy_path = index, path

    def decode(self, n: int, w: int | None = None, h: int | None = None) -> QImage:
        w, h = (w, h) if w and h else self.size
        if self.proxy_path and self.proxy_index:
            arr, _, _ = decode_frame_rgb(
                self.info, self.proxy_index, n, w, h, source_path=self.proxy_path, color_opts=PROXY_COLOR, pre=False
            )
        else:
            arr, _, _ = decode_frame_rgb(self.info, self.index, n, w, h)
        return np_to_qimage(arr)

    def pipe_cmd(self, start: int, w: int, h: int) -> list[str]:
        if self.proxy_path and self.proxy_index:
            src, idx = self.proxy_path, self.proxy_index
            vf = f"scale={w}:{h}:flags=bilinear:{PROXY_COLOR}:out_range=full,format=rgb24"
        else:
            src, idx = self.info.path, self.index
            vf = ",".join(
                pre_filters(self.info)
                + [
                    f"scale={w}:{h}:flags=bilinear+accurate_rnd+full_chroma_int:{in_color_opts(self.info)}:out_range=full",
                    "format=rgb24",
                ]
            )
        return [
            ffbin.ffmpeg(),
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{idx.seek_time(start):.6f}",
            "-i",
            src,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            vf,
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]


class FrameService(QObject):
    """단일 프레임 요청(스크럽/스텝). 마지막 요청만 처리(요청 합치기)."""

    frameReady = Signal(int, object)

    def __init__(self, decoder: Decoder):
        super().__init__()
        self.decoder = decoder
        self._cv = threading.Condition()
        self._req: tuple[int, int | None, int | None] | None = None
        self._stop = False
        self._cache: collections.OrderedDict = collections.OrderedDict()
        self._clock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="ics-frames", daemon=True)
        self._thread.start()

    def clear_cache(self) -> None:
        with self._clock:
            self._cache.clear()

    def request(self, n: int, w: int | None = None, h: int | None = None) -> None:
        key = (n, w, h)
        with self._clock:
            img = self._cache.get(key)
            if img is not None:
                self._cache.move_to_end(key)
        if img is not None:
            self.frameReady.emit(n, img)
            return
        with self._cv:
            self._req = key
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._req is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                key, self._req = self._req, None
            try:
                img = self.decoder.decode(*key)
            except Exception:  # noqa: BLE001
                continue
            with self._clock:
                self._cache[key] = img
                while len(self._cache) > 160:
                    self._cache.popitem(last=False)
            self.frameReady.emit(key[0], img)

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()


# ======================================================================= 썸네일
class ThumbService(QObject):
    """타임라인 썸네일: 디스크 캐시 재사용, 보이는 영역 우선, 유휴 시 전체 영상 거친 격자를 백그라운드 생성."""

    thumbReady = Signal(int)
    THUMB_H = 54

    def __init__(self, decoder: Decoder, cache_dir: Path, info: MediaInfo):
        super().__init__()
        self.decoder = decoder
        self.dir = Path(cache_dir) / "thumbs" / _cache_key(info)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.th = self.THUMB_H
        self.tw = max(8, int(round(self.th * info.display_w / info.display_h)))
        self._mem: collections.OrderedDict[int, QImage] = collections.OrderedDict()
        self._lock = threading.Lock()
        self._cv = threading.Condition()
        self._high: collections.deque[int] = collections.deque()
        self._low: collections.deque[int] = collections.deque()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="ics-thumbs", daemon=True)
        self._thread.start()
        n = decoder.index.count
        step = max(1, n // 120)
        self._low.extend(range(0, n, step))

    def _path(self, n: int) -> Path:
        return self.dir / f"{n:08d}.jpg"

    def get(self, n: int) -> QImage | None:
        with self._lock:
            im = self._mem.get(n)
            if im is not None:
                self._mem.move_to_end(n)
                return im
        p = self._path(n)
        if p.is_file():
            im = QImage(str(p))
            if not im.isNull():
                self._remember(n, im)
                return im
        return None

    def _remember(self, n: int, im: QImage) -> None:
        with self._lock:
            self._mem[n] = im
            while len(self._mem) > 600:
                self._mem.popitem(last=False)

    def nearest(self, n: int, radius: int) -> QImage | None:
        im = self.get(n)
        if im is not None:
            return im
        return None

    def request_visible(self, frames: list[int]) -> None:
        with self._cv:
            self._high = collections.deque(f for f in frames if not self._path(f).is_file() and f not in self._mem)
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stop and not self._high and not self._low:
                    self._cv.wait()
                if self._stop:
                    return
                n = self._high.popleft() if self._high else self._low.popleft()
            if self._path(n).is_file():
                if n not in self._mem:
                    self.get(n)
                continue
            try:
                im = self.decoder.decode(n, self.tw, self.th)
                im.save(str(self._path(n)), "JPG", 85)
                self._remember(n, im)
                self.thumbReady.emit(n)
            except Exception:  # noqa: BLE001
                pass
            if not self._high:
                time.sleep(0.005)

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()


# ======================================================================= 재생
class FramePipe(threading.Thread):
    """ffmpeg 에서 연속 프레임을 읽어 큐에 넣는다 (순차 디코딩 - 재생용)."""

    def __init__(self, decoder: Decoder, start: int, w: int, h: int):
        super().__init__(daemon=True, name="ics-pipe")
        self.q: queue.Queue = queue.Queue(maxsize=18)
        self.start_frame, self.w, self.h = start, w, h
        self._stop = threading.Event()
        self.proc = ffbin.popen(
            decoder.pipe_cmd(start, w, h),
            stdout=__import__("subprocess").PIPE,
            stderr=__import__("subprocess").DEVNULL,
            stdin=__import__("subprocess").DEVNULL,
        )

    def run(self) -> None:
        size = self.w * self.h * 3
        n = self.start_frame
        try:
            while not self._stop.is_set():
                buf = self.proc.stdout.read(size)
                if len(buf) < size:
                    break
                arr = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
                img = np_to_qimage(arr)
                while not self._stop.is_set():
                    try:
                        self.q.put((n, img), timeout=0.1)
                        break
                    except queue.Full:
                        continue
                n += 1
        finally:
            self.q.put(None) if not self._stop.is_set() else None

    def close(self) -> None:
        self._stop.set()
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass


class AudioPlayer:
    """프록시 WAV 를 QAudioSink 로 재생(정배속 전용). 오디오 장치가 없으면 조용히 비활성."""

    def __init__(self, wav_path: str | None):
        self.ok = False
        self._sink = None
        self._file = None
        self._offset = 0
        self._path = wav_path
        if not wav_path or not os.path.isfile(wav_path):
            return
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

            dev = QMediaDevices.defaultAudioOutput()
            if dev.isNull():
                return
            fmt = QAudioFormat()
            fmt.setSampleRate(48000)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self._sink = QAudioSink(dev, fmt)
            self._offset = self._data_offset(wav_path)
            self.ok = self._offset > 0
        except Exception:  # noqa: BLE001
            self.ok = False

    @staticmethod
    def _data_offset(path: str) -> int:
        with open(path, "rb") as f:
            head = f.read(12)
            if head[:4] != b"RIFF":
                return 0
            pos = 12
            while True:
                f.seek(pos)
                ch = f.read(8)
                if len(ch) < 8:
                    return 0
                cid, sz = ch[:4], struct.unpack("<I", ch[4:])[0]
                if cid == b"data":
                    return pos + 8
                pos += 8 + sz + (sz & 1)

    def play(self, seconds: float) -> None:
        if not self.ok:
            return
        try:
            from PySide6.QtCore import QFile, QIODevice

            self.stop()
            f = QFile(self._path)
            if not f.open(QIODevice.OpenModeFlag.ReadOnly):
                return
            byte = int(max(0.0, seconds) * 48000) * 4
            f.seek(self._offset + byte)
            self._file = f
            self._sink.start(f)
        except Exception:  # noqa: BLE001
            self.ok = False

    def stop(self) -> None:
        try:
            if self._sink is not None:
                self._sink.stop()
            if self._file is not None:
                self._file.close()
                self._file = None
        except Exception:  # noqa: BLE001
            pass


class PlaybackEngine(QObject):
    """실시간 재생(정/역/배속), 구간 반복. 화면 갱신은 imageReady 로 전달."""

    imageReady = Signal(int, object)
    stateChanged = Signal(bool)
    positionChanged = Signal(int)

    def __init__(self, decoder: Decoder, frames: FrameService):
        super().__init__()
        self.decoder, self.frames = decoder, frames
        self.index = decoder.index
        self.playing = False
        self.rate = 1.0
        self.loop: tuple[int, int] | None = None
        self.current = 0
        self._pipe: FramePipe | None = None
        self._base_wall = 0.0
        self._base_time = Fraction(0)
        self._timer = QTimer(self)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._tick)
        self.audio = AudioPlayer(None)
        self.audio_enabled = True
        self._last_shown = -1
        self._pending = None

    def set_audio(self, wav: str | None) -> None:
        self.audio = AudioPlayer(wav)

    def play(self, start: int, rate: float = 1.0, loop: tuple[int, int] | None = None) -> None:
        self.stop_pipe()
        self.rate, self.loop = rate, loop
        self.current = self.index.clamp(start)
        self._base_wall = time.perf_counter()
        self._base_time = self.index.time_of(self.current)
        self.playing = True
        if rate > 0:
            w, h = self.decoder.size
            self._pipe = FramePipe(self.decoder, self.current, w, h)
            self._pipe.start()
            if rate == 1.0 and self.audio_enabled:
                self.audio.play(float(self._base_time))
            else:
                self.audio.stop()
        else:
            self.audio.stop()
        self._timer.start()
        self.stateChanged.emit(True)

    def stop_pipe(self) -> None:
        self._pending = None
        if self._pipe:
            self._pipe.close()
            self._pipe = None

    def pause(self) -> None:
        if not self.playing:
            return
        self.playing = False
        self._timer.stop()
        self.stop_pipe()
        self.audio.stop()
        self.stateChanged.emit(False)

    def toggle(self, start: int, loop=None) -> None:
        if self.playing:
            self.pause()
        else:
            self.play(start, 1.0, loop)

    def _tick(self) -> None:
        now = time.perf_counter()
        t = self._base_time + Fraction((now - self._base_wall) * self.rate).limit_denominator(100000)
        end_t = self.index.time_of(self.index.count)
        target = self.index.frame_at(max(Fraction(0), t))
        if self.rate > 0:
            limit = (self.loop[1] - 1) if self.loop else self.index.count - 1
            if target >= limit or t >= end_t:
                if self.loop:
                    self.play(self.loop[0], self.rate, self.loop)
                    return
                self.current = self.index.count - 1
                self.positionChanged.emit(self.current)
                self.pause()
                return
        else:
            low = self.loop[0] if self.loop else 0
            if target <= low or t <= 0:
                self.current = low
                self.frames.request(low)
                self.positionChanged.emit(low)
                self.pause()
                return
        if self.rate > 0 and self._pipe is not None:
            shown = None
            while True:
                item = self._pending
                self._pending = None
                if item is None:
                    try:
                        item = self._pipe.q.get_nowait()
                    except queue.Empty:
                        break
                if item is None:  # 스트림 종료 표식
                    break
                if item[0] <= target:
                    shown = item
                else:
                    self._pending = item  # 아직 표시 시각이 안 된 프레임은 보관
                    break
            if shown:
                self.current = shown[0]
                self._last_shown = shown[0]
                self.imageReady.emit(shown[0], shown[1])
                self.positionChanged.emit(shown[0])
        elif self.rate < 0:
            if target != self.current:
                self.current = target
                self.frames.request(target)
                self.positionChanged.emit(target)

    def shutdown(self) -> None:
        self.pause()
