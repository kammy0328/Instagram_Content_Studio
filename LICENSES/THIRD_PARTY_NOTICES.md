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
| OpenCV (opencv-python-headless) | 인물/얼굴 검출 실행 (DNN) | Apache 2.0 |
| NanoDet-Plus 모델 (OpenCV Zoo `object_detection_nanodet_2022nov.onnx`) | STORY/REELS 인물 기준 자동 맞춤: 인물 검출 | Apache 2.0 |
| YuNet 모델 (OpenCV Zoo `face_detection_yunet_2023mar.onnx`) | STORY/REELS 인물 기준 자동 맞춤: 얼굴 검출 | MIT (Copyright (c) 2020 Shiqi Yu) |
| Lucide 아이콘 (lucide-static 0.460.0, `assets/icons`) | 화면의 선 아이콘 | ISC (전문: `assets/icons/LUCIDE_LICENSE.txt`) |
| Pretendard (Regular/Medium/SemiBold/Bold, `assets/fonts`) | 화면 글꼴, STORY 글자 | SIL OFL 1.1 (`assets/fonts/Pretendard_LICENSE_OFL.txt`) |

## FFmpeg / x264 (GPL) 배포 시 유의 사항

- libx264 가 포함된 FFmpeg 빌드는 GPL 입니다. 이 빌드를 설치 파일에 포함해 외부에 배포하면
  해당 FFmpeg 빌드의 소스 코드를 받을 수 있는 방법(배포처 링크 등)을 함께 안내해야 합니다.
- 본 프로그램은 FFmpeg 를 별도 실행 파일로 호출합니다 (라이브러리로 링크하지 않음).
- Windows 배포본(GitHub Actions 빌드)에는 BtbN FFmpeg-Builds 의 `win64-gpl-shared` 빌드(ffmpeg.exe, ffprobe.exe, DLL)가
  포함됩니다. 빌드 스크립트와 소스: https://github.com/BtbN/FFmpeg-Builds , FFmpeg 소스: https://ffmpeg.org/download.html
  (해당 빌드의 라이선스 전문은 설치 폴더의 `LICENSES/FFMPEG_LICENSE.txt` 에 함께 들어갑니다.)
- 상업적 배포 계획이 있다면 법무 검토 후 LGPL 빌드 + 별도 H.264 인코더 사용 여부를 결정하세요.

## PySide6 (LGPL 3)

- PyInstaller 로 묶은 폴더 배포(onedir) 방식을 사용하므로 Qt 라이브러리를 사용자가 교체할 수 있습니다.

## 인물 검출 모델 (assets/models)

- 출처: https://github.com/opencv/opencv_zoo (models/object_detection_nanodet, models/face_detection_yunet). 파일은 수정 없이
  그대로 포함합니다. 라이선스 전문은 `LICENSES/NANODET_LICENSE.txt` (Apache 2.0), `LICENSES/YUNET_LICENSE.txt` (MIT).
- SHA-256: nanodet `4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186`,
  yunet `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
- 실행 중 네트워크를 쓰지 않습니다 (모델은 설치 폴더에 포함).
