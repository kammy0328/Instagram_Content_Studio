"""Instagram Content Studio - 마스터링 영상 -> 인스타그램 게시용 콘텐츠 변환 도구."""

APP_VERSION_BASE = "1.3"  # 큰 변경 때만 손으로 올린다. 마지막 자리는 빌드마다 CI 가 자동으로 붙인다 (1.3.<빌드 번호>)
APP_NAME = "Instagram Content Studio"
APP_ID = "InstagramContentStudio"

try:  # CI 가 빌드마다 ics/_build.py 를 만든다 (빌드 ID, 채널). 소스 실행에서는 "dev".
    from ._build import BUILD_CHANNEL, BUILD_ID
except ImportError:
    BUILD_ID, BUILD_CHANNEL = "dev", "dev"
try:  # CI 빌드 번호 (빌드마다 1씩 증가)
    from ._build import BUILD_NUMBER
except ImportError:
    BUILD_NUMBER = 0
__version__ = f"{APP_VERSION_BASE}.{BUILD_NUMBER}" if BUILD_NUMBER else f"{APP_VERSION_BASE}.0-dev"

try:  # 비공개 저장소용 읽기 전용 토큰 (선택). CI 시크릿 UPDATE_READ_TOKEN 이 있을 때만 빌드에 들어간다.
    from ._build import BUILD_TOKEN
except ImportError:
    BUILD_TOKEN = ""
