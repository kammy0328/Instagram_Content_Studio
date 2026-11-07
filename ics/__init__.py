"""Instagram Content Studio - 마스터링 영상 -> 인스타그램 게시용 콘텐츠 변환 도구."""

__version__ = "1.1.0"
APP_NAME = "Instagram Content Studio"
APP_ID = "InstagramContentStudio"

try:  # CI 가 빌드마다 ics/_build.py 를 만든다 (빌드 ID, 채널). 소스 실행에서는 "dev".
    from ._build import BUILD_CHANNEL, BUILD_ID
except ImportError:
    BUILD_ID, BUILD_CHANNEL = "dev", "dev"
try:  # 비공개 저장소용 읽기 전용 토큰 (선택). CI 시크릿 UPDATE_READ_TOKEN 이 있을 때만 빌드에 들어간다.
    from ._build import BUILD_TOKEN
except ImportError:
    BUILD_TOKEN = ""
