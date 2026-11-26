from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture()
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("has_error", [False, True])
def test_audio_error_uses_value_across_qt_enum_namespaces(qapp, tmp_path, monkeypatch, has_error):
    from enum import Enum

    from PySide6 import QtMultimedia

    from ics.ui import services
    from tests.helpers import make_audio

    class SinkError(Enum):
        NoError = 0
        OpenError = 1

    class Device:
        def description(self):
            return "테스트 출력 장치"

    class Sink:
        def __init__(self, *args):
            pass

        def start(self, file):
            pass

        def setVolume(self, v):  # noqa: N802 (QAudioSink 와 같은 이름)
            self.volume = v

        def stop(self):
            pass

        def error(self):
            return SinkError.OpenError if has_error else SinkError.NoError

    wav = tmp_path / "audio.wav"
    make_audio(wav, 0.2)
    monkeypatch.setattr(services, "default_audio_output", Device)
    monkeypatch.setattr(QtMultimedia, "QAudioSink", Sink)
    player = services.AudioPlayer(str(wav))
    try:
        player.play(0.0)
        assert bool(player.error) is has_error
    finally:
        player.stop()


def test_play_pause_reuses_the_audio_output_for_instant_response(qapp, tmp_path, monkeypatch):
    """재생/정지가 즉시 반응하도록: 정지는 일시정지(suspend), 같은 자리 재생은 resume, 다른 자리는 같은 싱크에서 다시 시작.
    출력 장치가 바뀌었을 때만 싱크를 새로 만든다."""
    from enum import Enum

    from PySide6 import QtMultimedia

    from ics.ui import services
    from tests.helpers import make_audio

    class E(Enum):
        NoError = 0

    made, log = [], []

    class Dev:
        def __init__(self, name):
            self.name = name

        def id(self):
            return self.name.encode()

        def description(self):
            return self.name

    class Sink:
        def __init__(self, *a):
            made.append(self)
            self.t = 0

        def setBufferSize(self, n):  # noqa: N802
            log.append(("buffer", n))

        def start(self, f):
            log.append(("start", f.pos()))

        def stop(self):
            log.append(("stop",))

        def suspend(self):
            log.append(("suspend",))

        def resume(self):
            log.append(("resume",))

        def processedUSecs(self):  # noqa: N802
            return 500_000  # 0.5초 재생됨

        def setVolume(self, v):  # noqa: N802
            pass

        def error(self):
            return E.NoError

    wav = tmp_path / "a.wav"
    make_audio(wav, 3.0)
    dev = {"d": Dev("스피커")}
    monkeypatch.setattr(services, "default_audio_output", lambda: dev["d"])
    monkeypatch.setattr(QtMultimedia, "QAudioSink", Sink)
    p = services.AudioPlayer(str(wav))
    p.play(1.0)
    assert len(made) == 1 and any(x[0] == "buffer" and x[1] <= 48000 * 4 * 0.1 for x in log)
    p.stop()
    assert log[-1] == ("suspend",) and abs(p._paused_at - 1.5) < 1e-6
    p.play(1.52)  # 멈춘 자리: 이어서
    assert log[-1] == ("resume",) and len(made) == 1
    p.stop()
    p.play(0.2)  # 다른 자리: 같은 싱크에서 위치만 옮겨 다시
    assert log[-2:] == [("stop",), ("start", p._offset + int(0.2 * 48000) * 4)] and len(made) == 1
    dev["d"] = Dev("헤드셋")  # 장치가 바뀌면 새로
    p.stop()
    p.play(0.2)
    assert len(made) == 2
    p.close()
    assert p._sink is None and p._file is None
