"""앱 설정(JSON): 단축키 사용자 지정 등."""

from __future__ import annotations

import json
import os

from .. import paths

DEFAULT_SHORTCUTS = {
    "play_pause": ("재생/정지", "Space"),
    "shuttle_back": ("역재생 (J)", "J"),
    "shuttle_stop": ("정지 (K)", "K"),
    "shuttle_fwd": ("정/배속 재생 (L)", "L"),
    "step_back": ("1프레임 뒤로", "Left"),
    "step_fwd": ("1프레임 앞으로", "Right"),
    "step_back_10": ("10프레임 뒤로", "Shift+Left"),
    "step_fwd_10": ("10프레임 앞으로", "Shift+Right"),
    "goto_start": ("처음으로", "Home"),
    "goto_end": ("끝으로", "End"),
    "prev_cut": ("이전 컷", "Up"),
    "next_cut": ("다음 컷", "Down"),
    "set_in": ("In 지점 (I)", "I"),
    "set_out": ("Out 지점 (O)", "O"),
    "loop_play": ("선택 구간 반복 재생", "Shift+Space"),
    "undo": ("실행 취소", "Ctrl+Z"),
    "redo": ("다시 실행", "Ctrl+Shift+Z"),
    "zoom_in": ("타임라인 확대", "="),
    "zoom_out": ("타임라인 축소", "-"),
    "zoom_fit": ("타임라인 전체 보기", "Shift+Z"),
    "add_still": ("피드 스틸 추가", "S"),
    "add_video": ("피드 영상 추가", "V"),
    "merge_cuts": ("선택 컷 병합", "M"),
    "toggle_view": ("소스/출력 미리보기 전환", "Tab"),
    "toggle_mode": ("롤/트림 전환", "R"),
    "toggle_snap": ("스냅 켜기/끄기", "N"),
    "tab_story": ("STORY 탭", "Ctrl+1"),
    "tab_reels": ("REELS 탭", "Ctrl+2"),
    "tab_feed": ("FEED 탭", "Ctrl+3"),
    "tab_credits": ("CREDITS 탭", "Ctrl+4"),
}


class Settings:
    def __init__(self, path=None):
        self.path = path or paths.settings_path()
        self.data: dict = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        try:
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def shortcut(self, name: str) -> str:
        return self.data.get("shortcuts", {}).get(name, DEFAULT_SHORTCUTS[name][1])

    def set_shortcut(self, name: str, seq: str) -> None:
        sc = dict(self.data.get("shortcuts", {}))
        sc[name] = seq
        self.set("shortcuts", sc)

    def reset_shortcuts(self) -> None:
        self.set("shortcuts", {})
