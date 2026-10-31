"""편집 정보(XML) 불러오기: Premiere/FCP7 (xmeml), FCPXML, CMX3600 EDL.
XML 은 '참고 데이터'이며 원본 영상/XML 파일은 절대 수정하지 않는다."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .timecode import snap_fps, tc_to_frame


@dataclass
class XmlClip:
    start: int  # 시퀀스 시작(0) 기준 프레임, 포함
    end: int  # 미포함
    name: str = ""
    src_in: int = 0
    src_out: int = 0
    estimated: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class XmlTrack:
    index: int
    label: str
    clips: list[XmlClip] = field(default_factory=list)


@dataclass
class XmlSequence:
    name: str
    fps: Fraction
    tc_start: int = 0  # 시퀀스 시작 타임코드(프레임)
    duration: int = 0
    tracks: list[XmlTrack] = field(default_factory=list)

    def best_track(self) -> XmlTrack | None:
        if not self.tracks:
            return None
        return max(self.tracks, key=lambda t: (len(t.clips), -t.index))


@dataclass
class XmlDoc:
    path: str
    format: str  # 'xmeml' | 'fcpxml' | 'edl'
    sequences: list[XmlSequence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class XmlError(Exception):
    pass


def _txt(el, tag, default=None):
    if el is None:
        return default
    c = el.find(tag)
    return c.text.strip() if c is not None and c.text is not None else default


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def parse_file(path: str, edl_fps: Fraction | None = None) -> XmlDoc:
    p = Path(path)
    if not p.is_file():
        raise XmlError(f"파일을 찾을 수 없습니다: {path}")
    ext = p.suffix.lower()
    head = p.read_bytes()[:4096].decode("utf-8", "replace")
    if ext == ".edl" or re.search(r"^\s*TITLE:", head, re.M) and "FCM:" in head:
        return parse_edl(path, edl_fps or Fraction(24))
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise XmlError(f"XML 구문 오류: {e}") from e
    tag = root.tag.lower()
    if tag == "xmeml":
        return _parse_xmeml(root, path)
    if tag == "fcpxml":
        return _parse_fcpxml(root, path)
    raise XmlError("지원하지 않는 XML 형식입니다 (Premiere/FCP7 xmeml, FCPXML, EDL 지원).")


# ============================================================================ xmeml
def _rate_to_fps(rate_el) -> Fraction:
    tb = _int(_txt(rate_el, "timebase"), 24)
    ntsc = (_txt(rate_el, "ntsc", "FALSE") or "").upper() == "TRUE"
    return snap_fps(Fraction(tb * 1000, 1001) if ntsc else Fraction(tb))


def _parse_xmeml(root: ET.Element, path: str) -> XmlDoc:
    doc = XmlDoc(path, "xmeml")
    parent = {c: p for p in root.iter() for c in p}
    seqs = []
    for s in root.iter("sequence"):
        # 클립 안에 중첩된 시퀀스(참조)는 제외
        anc, skip = parent.get(s), False
        while anc is not None:
            if anc.tag in ("clipitem", "media", "generatoritem"):
                skip = True
                break
            anc = parent.get(anc)
        if not skip:
            seqs.append(s)
    if not seqs:
        raise XmlError("XML 에서 시퀀스를 찾지 못했습니다.")
    for s in seqs:
        fps = _rate_to_fps(s.find("rate"))
        tc_el = s.find("timecode")
        tcf = _int(_txt(tc_el, "frame"), 0) if tc_el is not None else 0
        seq = XmlSequence(
            name=_txt(s, "name", "Sequence") or "Sequence", fps=fps, tc_start=tcf, duration=_int(_txt(s, "duration"), 0)
        )
        vtracks = s.findall("./media/video/track")
        for ti, tr in enumerate(vtracks, start=1):
            track = XmlTrack(ti, f"V{ti}")
            items = [e for e in tr if e.tag in ("clipitem", "generatoritem", "transitionitem")]
            raw: list[dict] = []
            for e in items:
                raw.append(
                    {
                        "tag": e.tag,
                        "start": _int(_txt(e, "start"), -1),
                        "end": _int(_txt(e, "end"), -1),
                        "name": _txt(e, "name", "") or "",
                        "in": _int(_txt(e, "in"), 0),
                        "out": _int(_txt(e, "out"), 0),
                        "enabled": (_txt(e, "enabled", "TRUE") or "").upper() != "FALSE",
                        "align": _txt(e, "alignment", ""),
                    }
                )
            for i, r in enumerate(raw):
                if r["tag"] == "transitionitem" or not r["enabled"]:
                    continue
                st, en, est = r["start"], r["end"], False
                if st < 0:  # 트랜지션에 걸린 클립: 인접 트랜지션으로 경계 추정
                    tr_prev = raw[i - 1] if i > 0 and raw[i - 1]["tag"] == "transitionitem" else None
                    if tr_prev:
                        st = (
                            tr_prev["start"]
                            if tr_prev["align"] in ("start", "start-black")
                            else (tr_prev["start"] + tr_prev["end"]) // 2
                        )
                    est = True
                if en < 0:
                    tr_next = raw[i + 1] if i + 1 < len(raw) and raw[i + 1]["tag"] == "transitionitem" else None
                    if tr_next:
                        en = (
                            tr_next["end"]
                            if tr_next["align"] in ("end", "end-black")
                            else (tr_next["start"] + tr_next["end"]) // 2
                        )
                    est = True
                if st < 0 or en <= st:
                    continue
                track.clips.append(XmlClip(st, en, r["name"], r["in"], r["out"], est))
            track.clips.sort(key=lambda c: c.start)
            if track.clips:
                seq.tracks.append(track)
        if not seq.duration and seq.tracks:
            seq.duration = max(c.end for t in seq.tracks for c in t.clips)
        doc.sequences.append(seq)
    if any(c.estimated for q in doc.sequences for t in q.tracks for c in t.clips):
        doc.warnings.append("트랜지션이 포함된 컷은 경계를 추정했습니다. 타임라인에서 확인하세요.")
    return doc


# ============================================================================ fcpxml
def _fcp_time(s: str | None) -> Fraction | None:
    if not s:
        return None
    s = s.strip()
    if s.endswith("s"):
        s = s[:-1]
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def _parse_fcpxml(root: ET.Element, path: str) -> XmlDoc:
    doc = XmlDoc(path, "fcpxml")
    formats = {}
    for f in root.iter("format"):
        fd = _fcp_time(f.get("frameDuration"))
        if fd:
            formats[f.get("id")] = 1 / fd
    seqs = list(root.iter("sequence"))
    if not seqs:
        raise XmlError("FCPXML 에서 시퀀스를 찾지 못했습니다.")
    clip_tags = {"clip", "asset-clip", "video", "ref-clip", "mc-clip", "sync-clip", "title", "audition"}
    for i, s in enumerate(seqs, start=1):
        fps = snap_fps(formats.get(s.get("format"), Fraction(24)))
        tcs = _fcp_time(s.get("tcStart")) or Fraction(0)
        spine = s.find("spine")
        name = s.get("name") or (s.getparent().get("name") if hasattr(s, "getparent") else None) or f"Sequence {i}"
        seq = XmlSequence(name=name, fps=fps, tc_start=int(round(tcs * fps)))
        track = XmlTrack(1, "Primary")
        if spine is not None:
            for el in spine:
                if el.tag not in clip_tags:
                    continue
                off, dur = _fcp_time(el.get("offset")), _fcp_time(el.get("duration"))
                if off is None or dur is None:
                    continue
                st = int(round((off - tcs) * fps))
                en = int(round((off - tcs + dur) * fps))
                if en > st:
                    track.clips.append(
                        XmlClip(
                            st, en, el.get("name", "") or "", int(round((_fcp_time(el.get("start")) or 0) * fps)), 0
                        )
                    )
        if track.clips:
            seq.tracks.append(track)
            seq.duration = max(c.end for c in track.clips)
        doc.sequences.append(seq)
    doc.warnings.append("FCPXML 은 기본 스토리라인(spine)의 컷만 읽습니다. 커넥티드 클립/레인은 제외됩니다.")
    return doc


# ============================================================================ EDL
def parse_edl(path: str, fps: Fraction) -> XmlDoc:
    doc = XmlDoc(path, "edl")
    clips: list[XmlClip] = []
    drop = False
    first_rec: int | None = None
    for ln in Path(path).read_text("utf-8", errors="replace").splitlines():
        if ln.startswith("FCM:"):
            drop = "NON" not in ln.upper()
            continue
        m = re.match(
            r"^\s*(\d{3,6})\s+(\S+)\s+(\S+)\s+(\S+)\s+(?:\d+\s+)?"
            r"(\d\d:\d\d:\d\d[:;]\d\d)\s+(\d\d:\d\d:\d\d[:;]\d\d)\s+"
            r"(\d\d:\d\d:\d\d[:;]\d\d)\s+(\d\d:\d\d:\d\d[:;]\d\d)",
            ln,
        )
        if not m:
            continue
        if "V" not in m.group(3).upper() and m.group(3).upper() != "B":
            continue
        rin, rout = tc_to_frame(m.group(7), fps, drop), tc_to_frame(m.group(8), fps, drop)
        if rin is None or rout is None or rout <= rin:
            continue
        if first_rec is None:
            first_rec = rin
        clips.append(XmlClip(rin - first_rec, rout - first_rec, m.group(2)))
    if not clips:
        raise XmlError("EDL 에서 영상 이벤트를 찾지 못했습니다.")
    seq = XmlSequence(Path(path).stem, fps, tc_start=first_rec or 0, duration=max(c.end for c in clips))
    seq.tracks.append(XmlTrack(1, "V"))
    seq.tracks[0].clips = clips
    doc.sequences.append(seq)
    doc.warnings.append("EDL 에는 FPS 정보가 없어 마스터링 영상의 FPS 로 해석했습니다.")
    return doc


# ============================================================================ 매핑/검증
@dataclass
class MappingReport:
    offset: int
    cuts: list[tuple[int, int, str, dict]]
    warnings: list[str]
    clipped: int = 0


def map_to_master(
    seq: XmlSequence,
    track: XmlTrack,
    master_fps: Fraction,
    master_frames: int,
    offset: int = 0,
    master_tc_start: int | None = None,
) -> MappingReport:
    """시퀀스 트랙의 컷을 마스터링 프레임 좌표로 변환하고 불일치를 경고한다.
    master_frame = xml_frame(시퀀스 시작=0) + offset."""
    warns: list[str] = []
    scale = Fraction(1)
    if seq.fps != master_fps:
        if round(float(seq.fps)) == round(float(master_fps)):
            warns.append(
                f"시퀀스 FPS({float(seq.fps):.3f})와 마스터링 FPS({float(master_fps):.3f})의 표기가 다릅니다. "
                "프레임 번호를 1:1 로 대응시켰습니다."
            )
        else:
            scale = master_fps / seq.fps
            warns.append(
                f"시퀀스 FPS({float(seq.fps):.3f})와 마스터링 FPS({float(master_fps):.3f})가 달라 "
                "시간 기준으로 환산했습니다. 컷 경계를 반드시 확인하세요."
            )
    if master_tc_start is not None and master_tc_start != seq.tc_start:
        warns.append(
            f"시퀀스 시작 TC 프레임({seq.tc_start})과 마스터링 파일 TC 프레임({master_tc_start})이 다릅니다. "
            "오프셋을 확인하세요."
        )
    cuts = []
    clipped = 0
    for c in track.clips:
        s = int(round(c.start * scale)) + offset
        e = int(round(c.end * scale)) + offset
        s2, e2 = max(0, s), min(master_frames, e)
        if s2 != s or e2 != e:
            clipped += 1
        if e2 > s2:
            cuts.append(
                (s2, e2, c.name, {"xml_start": c.start, "xml_end": c.end, "src_in": c.src_in, "estimated": c.estimated})
            )
    if clipped:
        warns.append(f"{clipped}개 컷이 마스터링 영상 범위를 벗어나 잘렸거나 제외되었습니다.")
    xml_total = max((c.end for c in track.clips), default=0)
    xml_total_m = int(round(xml_total * scale))
    if xml_total_m + offset != master_frames:
        diff = master_frames - (xml_total_m + offset)
        warns.append(
            f"XML 총 길이({xml_total_m}f)+오프셋과 마스터링 길이({master_frames}f)가 {abs(diff)}프레임 "
            f"{'짧' if diff > 0 else '깁'}니다. 오프셋 또는 시퀀스 선택을 확인하세요."
        )
    starts = [c.start for c in track.clips]
    if starts and starts[0] != 0:
        warns.append(f"첫 컷이 시퀀스 {starts[0]}프레임에서 시작합니다 (앞부분 빈 구간).")
    return MappingReport(offset, cuts, warns, clipped)


def suggest_offset(seq: XmlSequence, track: XmlTrack, master_frames: int) -> int:
    """총 길이가 정확히 맞고 첫 컷이 0 이 아니면 그 차이만큼을 제안, 아니면 0."""
    if not track.clips:
        return 0
    first = track.clips[0].start
    last = track.clips[-1].end
    if first > 0 and (last - first) == master_frames:
        return -first
    return 0
