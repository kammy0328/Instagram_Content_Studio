"""Instagram Content Studio - 마스터링 영상 -> 인스타그램 게시용 콘텐츠 변환 도구."""

__version__ = "1.1.0"
APP_NAME = "Instagram Content Studio"
APP_ID = "InstagramContentStudio"

try:  # CI 가 빌드마다 ics/_build.py 를 만든다 (빌드 ID, 채널). 소스 실행에서는 "dev".
    from ._build import BUILD_CHANNEL, BUILD_ID
except ImportError:
    BUILD_ID, BUILD_CHANNEL = "dev", "dev"
