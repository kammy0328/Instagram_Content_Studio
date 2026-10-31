"""게시 전 파일/텍스트 검사. 공식 문서로 확정된 규칙(JPEG만)과 앱 자체 규칙만 '오류'로 취급한다.
Instagram 앱 수동 게시 규격을 API 규격으로 간주하지 않는다. 자동 자르기/무음화/길이 축소를 하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core import caption as cap
from .models import JobSpec, Kind, MediaAsset


@dataclass
class Finding:
    level: str  # error | warn | info
    message: str


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def add(self, level: str, msg: str) -> None:
        self.findings.append(Finding(level, msg))


def check_asset(a: MediaAsset, rep: ValidationReport, probe: bool = True) -> None:
    p = Path(a.path)
    if not p.is_file():
        rep.add("error", f"파일이 없습니다: {p.name}")
        return
    try:
        with p.open("rb") as f:
            head = f.read(65536)
    except OSError as e:
        rep.add("error", f"파일을 읽을 수 없습니다: {p.name} ({e.strerror or e})")
        return
    if a.sha256:
        from .models import MediaAsset as _M

        if _M.from_file(p, a.role).sha256 != a.sha256:
            rep.add("error", f"파일 내용이 불러온 뒤 바뀌었습니다: {p.name}")
    if a.role in ("image", "cover"):
        if not head.startswith(b"\xff\xd8\xff"):
            rep.add("error", f"JPEG 가 아닙니다 (공식 문서: 이미지는 JPEG만): {p.name}")
        elif b"MPF\x00" in head:
            rep.add("error", f"MPO 형식으로 보입니다 (공식 문서: MPO 불가): {p.name}")
    elif probe:
        try:
            from ..core import ffbin

            pj = ffbin.probe_json(str(p), "-show_streams", "-show_format")
            v = next((s for s in pj.get("streams", []) if s.get("codec_type") == "video"), None)
            if v is None:
                rep.add("error", f"영상 스트림이 없습니다: {p.name}")
                return
            has_audio = any(s.get("codec_type") == "audio" for s in pj.get("streams", []))
            dur = float(pj.get("format", {}).get("duration", 0) or 0)
            rep.add(
                "info",
                f"{p.name}: {v.get('codec_name')} {v.get('width')}x{v.get('height')} {v.get('pix_fmt')} "
                f"{dur:.1f}초 오디오 {'있음' if has_audio else '없음'}",
            )
        except Exception as e:  # noqa: BLE001
            rep.add("error", f"영상 검사에 실패했습니다: {p.name} ({e})")


def check_text(spec: JobSpec, rep: ValidationReport, limit: int = cap.DEFAULT_LIMIT) -> None:
    if spec.kind is Kind.STORY:
        if spec.caption or spec.first_comment:
            rep.add("error", "스토리에는 피드 캡션/댓글을 적용하지 않습니다.")
        return
    n = cap.count_chars(spec.caption)
    if n > limit:
        rep.add("error", f"캡션이 {n}자로 한도({limit}자)를 넘었습니다. 자동으로 자르지 않습니다.")
    tags = cap.count_hashtags(spec.caption)
    if tags > cap.HASHTAG_LIMIT:
        rep.add("error", f"해시태그가 {tags}개로 {cap.HASHTAG_LIMIT}개를 넘었습니다.")
    if spec.first_comment and cap.count_chars(spec.first_comment) > limit:
        rep.add("error", "첫 댓글이 한도를 넘었습니다.")


def validate_spec(spec: JobSpec, probe: bool = True) -> ValidationReport:
    rep = ValidationReport()
    if not spec.assets:
        rep.add("error", "게시할 미디어가 없습니다.")
    for a in spec.assets:
        check_asset(a, rep, probe)
    if spec.kind is Kind.FEED:
        roles = [a.role for a in spec.assets if a.role != "cover"]
        if len(roles) > 20:
            rep.add("error", "피드는 최대 20개까지 선택할 수 있습니다.")
    check_text(spec, rep)
    return rep


def missing_credits(project_credit_handles: list[str], caption: str) -> list[str]:
    """프로젝트 크레딧 대비 캡션에 빠진 후보를 알려 준다 (이름/계정을 추측해서 만들지 않는다)."""
    return [h for h in project_credit_handles if h and h not in caption]
