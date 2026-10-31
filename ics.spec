# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 명세 (Windows). 사용: pyinstaller --noconfirm ics.spec
# vendor/ffmpeg 폴더에 ffmpeg.exe, ffprobe.exe 가 있어야 한다 (vendor/ffmpeg/README.txt 참고).
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
root = os.path.abspath(SPECPATH)

# FFmpeg 는 실행 파일(+shared 빌드의 DLL)을 데이터로 복사한다. binaries 로 넣으면 PyInstaller 가 수백 MB 를
# 분석/중복 처리하려 하므로 datas 로 그대로 둔다. ffbin 이 _MEIPASS/vendor/ffmpeg 에서 찾는다.
ff_dir = os.path.join(root, "vendor", "ffmpeg")
ff_files = []
if os.path.isdir(ff_dir):
    for name in sorted(os.listdir(ff_dir)):
        low = name.lower()
        if (low.endswith(".exe") or low.endswith(".dll")) and low != "ffplay.exe":
            ff_files.append((os.path.join(ff_dir, name), os.path.join("vendor", "ffmpeg")))
if not any(os.path.basename(src).lower() == "ffmpeg.exe" for src, _ in ff_files):
    print(f"[ics.spec] 경고: {ff_dir} 에 ffmpeg.exe 가 없어 번들에서 제외됩니다 (설치 후 PATH 의 ffmpeg 를 사용).")

datas = [(os.path.join(root, "LICENSES"), "LICENSES")] + ff_files

a = Analysis(
    ["run.py"],
    pathex=[root],
    binaries=[],
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
