@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  Instagram Content Studio - Windows 빌드 스크립트
REM  사전 준비: Python 3.11~3.13 (64bit), vendor\ffmpeg\ffmpeg.exe / ffprobe.exe
REM  선택: Inno Setup 6 (설치 파일 .exe 생성)
REM ============================================================
cd /d "%~dp0"

if not exist vendor\ffmpeg\ffmpeg.exe (
  echo [오류] vendor\ffmpeg\ffmpeg.exe 가 없습니다. vendor\ffmpeg\README.txt 를 참고해 넣어 주세요.
  exit /b 1
)
if not exist vendor\ffmpeg\ffprobe.exe (
  echo [오류] vendor\ffmpeg\ffprobe.exe 가 없습니다.
  exit /b 1
)

if not exist .venv (
  py -3 -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat || goto :fail
python -m pip install --upgrade pip || goto :fail
pip install -r requirements-build.txt || goto :fail

echo.
echo [1/3] 테스트 실행 (실패하면 빌드를 중단합니다)
python -m pytest tests -q -x || goto :fail

echo.
echo [2/3] PyInstaller 빌드
pyinstaller --noconfirm ics.spec || goto :fail

echo.
echo [3/3] 설치 파일 생성 (Inno Setup 이 설치되어 있을 때만)
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
  %ISCC% installer\ics_installer.iss || goto :fail
  echo 설치 파일: installer\Output\InstagramContentStudio_Setup.exe
) else (
  echo Inno Setup 6 이 없어 설치 파일 생성을 건너뜁니다. dist\InstagramContentStudio 폴더를 그대로 배포할 수 있습니다.
)
echo.
echo 완료: dist\InstagramContentStudio\InstagramContentStudio.exe
exit /b 0

:fail
echo.
echo [실패] 위 메시지를 확인하세요.
exit /b 1
