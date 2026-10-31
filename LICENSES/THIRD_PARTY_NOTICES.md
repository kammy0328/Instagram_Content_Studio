# 서드파티 소프트웨어 고지

Instagram Content Studio 는 아래 소프트웨어를 사용합니다. 배포 시 각 라이선스 조건을 확인하세요.

| 구성 요소 | 용도 | 라이선스 |
|---|---|---|
| FFmpeg / FFprobe | 미디어 분석, 디코딩, 인코딩 | LGPL 2.1+ 또는 GPL 2+ (빌드 옵션에 따라 다름) |
| x264 (libx264) | H.264 인코딩 | GPL 2+ |
| PySide6 (Qt for Python) | UI | LGPL 3 |
| Pillow | JPG 저장, 이미지 처리 | HPND (MIT 계열) |
| NumPy | 배열 처리 | BSD 3-Clause |
| regex | 유니코드 글자 단위 계산 | Apache 2.0 |
| SQLite | 프로젝트 저장 | Public Domain |

## FFmpeg / x264 (GPL) 배포 시 유의 사항

- libx264 가 포함된 FFmpeg 빌드는 GPL 입니다. 이 빌드를 설치 파일에 포함해 외부에 배포하면
  해당 FFmpeg 빌드의 소스 코드를 받을 수 있는 방법(배포처 링크 등)을 함께 안내해야 합니다.
- 본 프로그램은 FFmpeg 를 별도 실행 파일로 호출합니다 (라이브러리로 링크하지 않음).
- 상업적 배포 계획이 있다면 법무 검토 후 LGPL 빌드 + 별도 H.264 인코더 사용 여부를 결정하세요.

## PySide6 (LGPL 3)

- PyInstaller 로 묶은 폴더 배포(onedir) 방식을 사용하므로 Qt 라이브러리를 사용자가 교체할 수 있습니다.
