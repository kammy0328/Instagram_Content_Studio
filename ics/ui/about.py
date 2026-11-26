"""프로그램 정보: 이름, 버전/빌드, 개발자, 구성 요소 버전, 오픈소스 고지."""

from __future__ import annotations

import platform

from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import APP_NAME, BUILD_CHANNEL, BUILD_ID, __version__
from . import theme
from .frameless import ChromeDialog
from .widgets import studio_icon

DEVELOPER = "이승우 (LEE SEUNGWOO)"
COMPANY = "DIAP · 디지털압구정"

try:  # CI 가 빌드할 때 기록한 날짜 (소스 실행에서는 없음)
    from .._build import BUILD_DATE
except ImportError:
    BUILD_DATE = ""


def version_text() -> str:
    b = BUILD_ID if BUILD_ID == "dev" else f"빌드 {BUILD_ID}"
    ch = "" if BUILD_CHANNEL in ("dev", "main") else f" · {BUILD_CHANNEL}"
    return f"v{__version__} · {b}{ch}" + (f" · {BUILD_DATE}" if BUILD_DATE else "")


def components() -> list[tuple[str, str]]:
    from ..core import ffbin

    ff = ffbin.versions().get("ffmpeg", "")
    ff = ff.split(" Copyright")[0].replace("ffmpeg version ", "") if ff else "-"
    return [
        ("Python", platform.python_version()),
        ("Qt", f"{qVersion()} (PySide6)"),
        ("FFmpeg", ff if not ff.startswith("unavailable") else "찾을 수 없음"),
        ("운영체제", f"{platform.system()} {platform.release()} ({platform.machine()})"),
    ]


class AboutDialog(ChromeDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("프로그램 정보")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 20)
        v.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(14)
        ic = QLabel()
        ic.setPixmap(studio_icon(52).pixmap(52, 52))
        head.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        tv = QVBoxLayout()
        tv.setSpacing(3)
        name = QLabel(APP_NAME)
        name.setObjectName("h1")
        tv.addWidget(name)
        sub = QLabel("마스터링 영상 하나로 STORY · REELS · FEED 게시물 만들기")
        sub.setObjectName("hint")
        tv.addWidget(sub)
        self.version_lbl = QLabel(version_text())
        self.version_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.version_lbl.setStyleSheet(f"font-weight:600; color:{theme.TEXT}; padding-top:4px;")
        tv.addWidget(self.version_lbl)
        head.addLayout(tv, 1)
        v.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        rows = [("개발", DEVELOPER), ("", COMPANY)] + components()
        self._rows = rows
        for i, (k, val) in enumerate(rows):
            kl = QLabel(k)
            kl.setObjectName("fieldLabel")
            vl = QLabel(val)
            vl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            vl.setWordWrap(True)
            if k == "개발":
                vl.setStyleSheet("font-weight:600;")
            grid.addWidget(kl, i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(vl, i, 1)
            if i == 1:  # 개발자와 구성 요소 사이 간격
                grid.setRowMinimumHeight(i, 26)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        cr = QLabel(f"© 2026 {COMPANY}. 오픈소스 구성 요소의 라이선스는 [오픈소스 고지]에서 볼 수 있습니다.")
        cr.setObjectName("hint")
        cr.setWordWrap(True)
        v.addWidget(cr)

        row = QHBoxLayout()
        b_lic = QPushButton("오픈소스 고지")
        b_lic.clicked.connect(self._open_licenses)
        row.addWidget(b_lic)
        b_copy = QPushButton("정보 복사")
        b_copy.setToolTip("오류를 알릴 때 붙여 넣을 수 있게 버전과 환경 정보를 복사합니다")
        b_copy.clicked.connect(self.copy_info)
        row.addWidget(b_copy)
        row.addStretch(1)
        ok = QPushButton("닫기")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        v.addLayout(row)

    def info_text(self) -> str:
        lines = [f"{APP_NAME} {version_text()}"] + [f"{k or '회사'}: {val}" for k, val in self._rows]
        return "\n".join(lines)

    def copy_info(self) -> None:
        QGuiApplication.clipboard().setText(self.info_text())
        sender = self.sender()
        if isinstance(sender, QPushButton):
            sender.setText("복사됨")

    def _open_licenses(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from .. import paths

        d = paths.resource_path("LICENSES")
        f = d / "THIRD_PARTY_NOTICES.md"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(f if f.is_file() else d)))
