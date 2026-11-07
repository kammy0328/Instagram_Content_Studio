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
