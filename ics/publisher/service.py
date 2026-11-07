"""Publisher 서비스 계층(Qt 없음): UI 가 부르는 하나의 진입점. 모든 자동 게시는 클라우드 중계를 거친다.

앱은 인스타그램 API 를 직접 호출하지 않는다(토큰은 중계 계정 저장소에만 암호화되어 보관). 직접 호출 경로가 실수로 쓰이지 않도록
엔진의 전송/호스트 자리에는 호출 시 예외를 내는 자리표시자를 넣는다."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import adapter
from .capability import FEATURES, CapabilityProfile
from .cloud import CloudCancelRefused, CloudScheduler
from .engine import Account, BatchReport, PublishEngine
from .models import ALL_KINDS, Kind, State
from .relay import RelayClient, RelayError
from .secrets import SecretStore
from .store import JobRow, PublisherStore

# 공식 문서로 고정할 Graph API 버전. 설정에서 바꿀 수 있지만 바뀌면 기능 프로필이 UNKNOWN 으로 돌아간다.
DEFAULT_API_VERSION = "v23.0"
IMMEDIATE_LEAD = 20  # '지금 게시'는 중계가 다음 주기에 실행하도록 20초 뒤로 예약


class _NoDirectCalls:
    is_simulation = False

    def __getattr__(self, name):
        def _f(*a, **k):
            raise RuntimeError("앱은 인스타그램을 직접 호출하지 않습니다. 게시는 클라우드 중계를 통해서만 실행됩니다.")

        return _f


@dataclass
class ReviewRow:
    kind: Kind
    job_id: int
    state: State
    files: list[str]
    caption: str
    first_comment: str
    blocked: str = ""


@dataclass
class SubmitResult:
    states: dict[int, State] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    not_started: list[int] = field(default_factory=list)


class PublisherService:
    def __init__(
        self,
        data_dir: Path,
        settings,
        secrets: SecretStore,
        relay_factory: Callable | None = None,
        clock=time.time,
        probe_media: bool = True,
    ):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings, self.secrets, self.clock, self.probe_media = settings, secrets, clock, probe_media
        self._relay_factory = relay_factory or (lambda url, key: RelayClient(url, key))
        self.store = PublisherStore(self.dir / "publisher.sqlite")
        self._engine: PublishEngine | None = None
        self._cloud: CloudScheduler | None = None

    # ------------------------------------------------------------ 설정
    def config(self) -> dict:
        g = lambda k, d="": self.settings.get("publisher_" + k, d)  # noqa: E731
        return {
            "relay_url": g("relay_url"),
            "ig_id": g("ig_id"),
            "username": g("username"),
            "api_version": g("api_version", DEFAULT_API_VERSION),
        }

    def save_config(self, relay_url: str, ig_id: str, username: str, api_version: str) -> None:
        for k, v in (
            ("relay_url", relay_url.strip()),
            ("ig_id", ig_id.strip()),
            ("username", username.strip()),
            ("api_version", api_version.strip()),
        ):
            self.settings.set("publisher_" + k, v)
        self._engine = self._cloud = None  # 다음 사용 때 새 설정으로 재구성

    def missing_setup(self) -> list[str]:
        c, out = self.config(), []
        if not c["relay_url"]:
            out.append("중계 주소")
        if not self.secrets.get("relay_api_key"):
            out.append("중계 API 키")
        if not c["ig_id"]:
            out.append("인스타그램 계정 ID")
        if not self.secrets.get("ig_token"):
            out.append("계정 토큰")
        return out

    # ------------------------------------------------------------ 기능 프로필
    def _profile_path(self) -> Path:
        return self.dir / "capability.json"

    def profile(self) -> CapabilityProfile:
        p = CapabilityProfile.load(self._profile_path())
        c = self.config()
        changed = p.reverify_if_changed(c["ig_id"], c["api_version"], p.permissions)
        if changed:
            p.save(self._profile_path())
        return p

    def mark_feature(self, feature: str, verified: bool, note: str, carousel_limit: int | None = None) -> None:
        """사용자가 실제 테스트 계정에서 확인한 결과를 기록한다(근거 메모 필수). 자동으로 켜지지 않는다."""
        if feature not in FEATURES:
            raise ValueError(feature)
        if verified and not note.strip():
            raise ValueError("확인 근거(어떤 테스트로 확인했는지)를 적어야 합니다.")
        p = self.profile()
        if verified:
            p.mark_verified(feature, "user-live-test", note.strip(), carousel_limit=carousel_limit)
        else:
            p.mark_unsupported(feature, "user", note.strip())
        p.save(self._profile_path())
        self._engine = self._cloud = None

    # ------------------------------------------------------------ 엔진/클라우드
    def _build(self) -> tuple[PublishEngine, CloudScheduler]:
        if self._engine is None or self._cloud is None:
            miss = self.missing_setup()
            if miss:
                raise RuntimeError("연결 설정이 부족합니다: " + ", ".join(miss))
            c = self.config()
            relay = self._relay_factory(c["relay_url"], self.secrets.get("relay_api_key"))
            eng = PublishEngine(
                self.store,
                _NoDirectCalls(),
                _NoDirectCalls(),
                self.profile(),
                Account(c["ig_id"], c["username"]),
                clock=self.clock,
                probe_media=self.probe_media,
            )
            self._engine = eng
            self._cloud = CloudScheduler(eng, relay, c["api_version"], lambda: self.secrets.get("ig_token"))
        return self._engine, self._cloud

    @property
    def cloud(self) -> CloudScheduler:
        return self._build()[1]

    # ------------------------------------------------------------ 준비/검토
    def load_output(self, root: Path) -> adapter.OutputBundle:
        return adapter.read_output(root)

    def prepare(self, root: Path, project_id: str, selection: set[Kind], first_comment: bool, policy: str = "stop"):
        eng, _ = self._build()
        bundle = adapter.read_output(root)
        specs = adapter.build_specs(bundle, project_id, first_comment=first_comment)
        report = eng.prepare_batch(specs, selection, policy)
        return report, self.review(report)

    def review(self, report: BatchReport) -> list[ReviewRow]:
        rows = []
        for k in ALL_KINDS:
            if k in report.job_ids:
                j = self.store.get(report.job_ids[k])
                d = j.spec
                rows.append(
                    ReviewRow(
                        k,
                        j.id,
                        j.state,
                        [
                            Path(p).name or a["role"]
                            for a, p in zip(d["assets"], d.get("_paths", [""] * len(d["assets"])))
                        ],
                        d["caption"],
                        d["first_comment"],
                        report.blocked.get(k, ""),
                    )
                )
        return rows

    # ------------------------------------------------------------ 승인 + 예약/즉시
    def approve_and_submit(self, report: BatchReport, schedule_at: float | None) -> SubmitResult:
        """schedule_at=None 이면 '지금 게시'(중계가 곧 실행). 정책 stop 이면 한 건이 막히면 나머지는 시작하지 않는다."""
        eng, cloud = self._build()
        with self.store._lock:  # noqa: SLF001
            r = self.store._c.execute(
                "SELECT policy FROM batches WHERE id=?", (report.batch_id,)
            ).fetchone()  # noqa: SLF001
        policy = r[0] if r else "stop"
        res, halted = SubmitResult(), False
        lead = IMMEDIATE_LEAD if schedule_at is None else None
        for k in ALL_KINDS:
            jid = report.job_ids.get(k)
            if jid is None:
                continue
            if self.store.get(jid).state is not State.AWAITING_APPROVAL:
                continue
            if halted:
                res.not_started.append(jid)
                continue
            try:
                when = schedule_at if schedule_at is not None else self.clock() + IMMEDIATE_LEAD
                eng.approve(jid, schedule_at=when)
                st = cloud.submit(jid, min_lead=lead) if lead else cloud.submit(jid)
                res.states[jid] = st
                if st is not State.CLOUD_SCHEDULED:
                    halted = halted or policy == "stop"
            except (RelayError, ValueError) as e:
                res.errors[jid] = getattr(e, "message", str(e))
                res.states[jid] = self.store.get(jid).state
                halted = halted or policy == "stop"
        return res

    def sync(self) -> dict[int, State]:
        return self.cloud.sync()

    def cancel(self, job_id: int) -> str:
        try:
            if self.cloud.is_cloud(job_id) and self.store.get(job_id).state is State.CLOUD_SCHEDULED:
                self.cloud.cancel(job_id)
            else:
                self._build()[0].cancel(job_id)
            return ""
        except CloudCancelRefused as e:
            return str(e)

    # ------------------------------------------------------------ 이력
    def history(self, text: str = "", kind: str | None = None, state: State | None = None) -> list[JobRow]:
        return self.store.search(text=text, kind=kind, state=state)

    # ------------------------------------------------------------ 수동 내보내기 (자동 게시가 막혔을 때의 대체 경로)
    def export_manual(self, root: Path, dest: Path, selection: set[Kind] | None = None) -> list[Path]:
        """선택한 유형을 업로드 순서(01, 02 ...)대로 복사하고 UTF-8 캡션/댓글/크레딧과 안내문을 만든다. 원본은 건드리지 않는다."""
        b = adapter.read_output(Path(root))
        sel = selection or set(ALL_KINDS)
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []

        def put(src: str, name: str) -> None:
            t = dest / name
            shutil.copy2(src, t)
            out.append(t)

        if Kind.STORY in sel and b.story:
            put(b.story.path, "STORY_01.mp4")
        if Kind.REELS in sel and b.reels:
            put(b.reels.path, "REELS_01.mp4")
            if b.reels_cover:
                put(b.reels_cover.path, "REELS_COVER.jpg")
        if Kind.FEED in sel:
            for n, a in enumerate(b.feed, start=1):
                put(a.path, f"FEED_{n:02d}_{'VIDEO.mp4' if a.role == 'video' else 'STILL.jpg'}")
        texts = {}
        if sel & {Kind.REELS, Kind.FEED}:
            texts["CAPTION.txt"] = b.caption
            for i, c in enumerate(b.comments, start=1):
                texts[f"COMMENT_{i:02d}.txt"] = c
            texts["CREDITS.txt"] = b.credits
        guide = [
            "수동 업로드 안내",
            "1. 인스타그램 앱에서 새 게시물/릴스/스토리를 만들고 아래 파일을 순서대로 선택합니다.",
            "2. FEED 는 번호(01, 02 ...) 순서대로 선택하세요.",
            "3. CAPTION.txt 내용을 복사해 캡션에 붙여 넣습니다(UTF-8, 줄바꿈 유지).",
            "4. 게시 후 COMMENT_*.txt 를 첫 댓글로 붙여 넣습니다. 스토리에는 캡션/댓글을 쓰지 않습니다.",
        ]
        texts["UPLOAD_GUIDE.txt"] = "\n".join(guide)
        for name, body in texts.items():
            (dest / name).write_text(body, "utf-8")
            out.append(dest / name)
        return out
