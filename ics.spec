# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 명세 (Windows). 사용: pyinstaller --noconfirm ics.spec
# vendor/ffmpeg 폴더에 ffmpeg.exe, ffprobe.exe 가 있어야 한다 (vendor/ffmpeg/README.txt 참고).
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
root = os.path.abspath(SPECPATH)

binaries = []
for exe in ("ffmpeg.exe", "ffprobe.exe"):
    p = os.path.join(root, "vendor", "ffmpeg", exe)
    if os.path.isfile(p):
        binaries.append((p, os.path.join("vendor", "ffmpeg")))
    else:
        print(f"[ics.spec] 경고: {p} 가 없어 번들에서 제외됩니다 (설치 후 PATH 의 ffmpeg 를 사용).")

datas = [(os.path.join(root, "LICENSES"), "LICENSES")]

a = Analysis(
    ["run.py"],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("ics") + ["regex"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="InstagramContentStudio",
    console=False,                # 콘솔 창 없음
    icon=None,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="InstagramContentStudio")
