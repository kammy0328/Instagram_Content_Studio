이 폴더에 FFmpeg 실행 파일 2개를 넣으세요 (Windows 64bit):

  vendor/ffmpeg/ffmpeg.exe
  vendor/ffmpeg/ffprobe.exe

요구 사항
  - libx264 와 AAC 인코더가 포함된 빌드 (GPL 빌드 권장: 예) BtbN FFmpeg-Builds 의 "win64-gpl" 또는 gyan.dev "full/essentials")
  - zimg(zscale) / soxr 가 있으면 더 좋지만 필수는 아닙니다.
  - 정적(static) 빌드(단일 exe) 권장.

라이선스 주의
  libx264 를 포함한 GPL 빌드를 배포물에 포함하면 FFmpeg/x264 의 GPL 의무(소스 제공 안내, 라이선스 문구 동봉)가 적용됩니다.
  LICENSES/THIRD_PARTY_NOTICES.md 를 참고하세요. 내부 용도로만 쓰더라도 배포 전에 라이선스를 확인하세요.

개발 중에는 이 폴더 대신 PATH 의 ffmpeg 를 쓰거나, 환경 변수 ICS_FFMPEG / ICS_FFPROBE 로 경로를 지정할 수 있습니다.
