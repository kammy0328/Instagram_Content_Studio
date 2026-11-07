"""로고 프리셋: 한 번 저장해 두면 어느 프로젝트에서든 눌러서 STORY 로고로 넣을 수 있다 (로고 이미지만 해당, 제목 텍스트는 프로젝트마다).

저장 위치: 앱 데이터 폴더의 logo_presets/ (프로젝트가 참조하는 파일이 앱 설치 위치와 무관하게 남도록 항상 이쪽으로 복사한다).
기본 제공: assets/logos 의 밝은색/어두운색 로고 (삭제 불가).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

BUILTIN = (
    ("diap 로고 (밝은색, 검정 여백용)", "diap_light.png"),
    ("diap 로고 (어두운색)", "diap_dark.png"),
)


@dataclass
class LogoPreset:
    name: str
    file: str  # 실제 이미지 경로 (내장은 번들 경로)
    size: int = 40
    pos: str = "bottom"
    builtin: bool = False


def _dir() -> Path:
    d = paths.app_data_dir() / "logo_presets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", name).strip("_")
    return s or "logo"


class LogoPresets:
    def __init__(self, settings):
        self.settings = settings

    def all(self) -> list[LogoPreset]:
        out = [
            LogoPreset(n, str(paths.resource_path("assets/logos") / f), 22, "bottom", True)
            for n, f in BUILTIN
            if (paths.resource_path("assets/logos") / f).is_file()
        ]
        for d in self.settings.get("logo_presets", []) or []:
            if isinstance(d, dict) and d.get("name") and Path(d.get("file", "")).is_file():
                out.append(LogoPreset(d["name"], d["file"], int(d.get("size", 22)), d.get("pos", "bottom")))
        return out

    def get(self, name: str) -> LogoPreset | None:
        return next((p for p in self.all() if p.name == name), None)

    def save(self, name: str, src: str, size: int, pos: str) -> LogoPreset:
        """이미지를 투명 여백을 잘라 앱 데이터에 복사하고 같은 이름이 있으면 덮어쓴다."""
        from .render.overlay import trim_logo

        name = name.strip()
        if not name:
            raise ValueError("프리셋 이름을 입력하세요.")
        if any(name == n for n, _f in BUILTIN):
            raise ValueError("기본 제공 프리셋과 같은 이름은 쓸 수 없습니다.")
        dst = trim_logo(src, _dir() / f"{_slug(name)}.png")
        lst = [d for d in (self.settings.get("logo_presets", []) or []) if d.get("name") != name]
        lst.append({"name": name, "file": str(dst), "size": int(size), "pos": pos})
        self.settings.set("logo_presets", lst)
        return LogoPreset(name, str(dst), int(size), pos)

    def delete(self, name: str) -> None:
        lst = self.settings.get("logo_presets", []) or []
        for d in lst:
            if d.get("name") == name:
                Path(d.get("file", "")).unlink(missing_ok=True)
        self.settings.set("logo_presets", [d for d in lst if d.get("name") != name])

    def materialize(self, p: LogoPreset) -> str:
        """프로젝트에 넣을 안정적인 경로. 내장 로고는 앱 데이터로 복사해 (설치 위치가 바뀌어도) 유지한다."""
        if not p.builtin:
            return p.file
        dst = _dir() / f"builtin_{Path(p.file).name}"
        if not dst.is_file() or dst.stat().st_size != Path(p.file).stat().st_size:
            dst.write_bytes(Path(p.file).read_bytes())
        return str(dst)
