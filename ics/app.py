"""앱 진입점: 프로젝트 목록 -> (분석/재연결) -> 편집 창."""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog

from . import APP_NAME, paths
from .core import ffbin
from .core.frameindex import build_frame_index
from .core.iddict import IdDictionary
from .core.media import analyze
from .core.project import Project
from .core.store import Library, ProjectRecord
from .ui import theme
from .ui.dialogs import MediaReportDialog, NewProjectDialog, ProjectListDialog
from .ui.main_window import EditorWindow
from .ui.settings import Settings
from .ui.widgets import studio_icon


class App:
    def __init__(self, argv=None):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.qt = QApplication.instance() or QApplication(argv or sys.argv)
        # 편집 창 <-> 프로젝트 목록 전환 중에는 열린 창이 잠시 없다. 기본값(True)이면 그 순간 앱이 통째로 종료되므로
        # 자동 종료를 끄고, 사용자가 편집 창을 닫거나 프로젝트 목록을 취소할 때만 명시적으로 종료한다.
        self.qt.setQuitOnLastWindowClosed(False)
        self._switching = False
        self.qt.setApplicationName(APP_NAME)
        theme.apply(self.qt)
        self.qt.setWindowIcon(studio_icon())
        self.settings = Settings()
        self.library = Library()
        self.iddict = IdDictionary()
        self.window: EditorWindow | None = None
        sys.excepthook = self._excepthook

    def _excepthook(self, et, ev, tb):
        text = "".join(traceback.format_exception(et, ev, tb))
        try:
            (paths.logs_dir() / "crash.log").open("a", encoding="utf-8").write(text + "\n")
        except OSError:
            pass
        QMessageBox.critical(
            None,
            "예기치 못한 오류",
            f"오류가 발생했습니다. 편집 내용은 자동저장되어 있습니다.\n\n{ev}\n\n(진단 로그: {paths.logs_dir()})",
        )

    # -------------------------------------------------------------- 흐름
    def run(self) -> int:
        try:
            ffbin.ffmpeg(), ffbin.ffprobe()
        except ffbin.FFmpegNotFound as e:
            QMessageBox.critical(None, "FFmpeg 없음", str(e))
            return 2
        QTimer.singleShot(0, self.show_projects)
        return self.qt.exec()

    def _quit(self) -> None:
        self.qt.quit()

    def show_projects(self) -> None:
        if self.window is not None:
            return
        self._switching = False
        dlg = ProjectListDialog(self.library)
        if not dlg.exec():
            self._quit()
            return
        if dlg.new_requested:
            self.new_project()
        elif dlg.chosen:
            self.open_record(dlg.chosen)
        else:
            self._quit()

    def new_project(self) -> None:
        dlg = NewProjectDialog(None, self.settings.get("last_output", ""))
        if not dlg.exec():
            QTimer.singleShot(0, self.show_projects)
            return
        v = dlg.values()
        self.settings.set("last_output", v["output_dir"])
        rec = self.library.create(v["name"], v["artist"], v["title"], v["master_path"], v["output_dir"])
        if dlg.info and (dlg.info.warnings or dlg.info.color_assumed):
            MediaReportDialog(dlg.info).exec()
        self.open_record(rec)

    def open_record(self, rec: ProjectRecord) -> None:
        # 원본 연결 확인 / 재연결
        while not os.path.isfile(rec.master_path):
            m = QMessageBox.question(
                self.window,
                "마스터링 파일을 찾을 수 없음",
                f"원본 파일이 이동되었거나 연결이 끊겼습니다.\n{rec.master_path}\n\n새 위치를 지정할까요?\n"
                "(NAS/외장 디스크라면 연결 상태를 먼저 확인하세요)",
            )
            if m != QMessageBox.StandardButton.Yes:
                QTimer.singleShot(0, self.show_projects)
                return
            p, _ = QFileDialog.getOpenFileName(
                None, "마스터링 영상 다시 연결", "", "영상 (*.mp4 *.mov *.m4v);;모든 파일 (*)"
            )
            if p:
                rec.master_path = p
                self.library.upsert(rec)
        prog = QProgressDialog("영상 분석 중…", None, 0, 0)
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setMinimumDuration(0)
        prog.show()
        self.qt.processEvents()
        try:
            info = analyze(rec.master_path)
            if info.errors:
                prog.close()
                MediaReportDialog(info).exec()
                QTimer.singleShot(0, self.show_projects)
                return
            cache = paths.cache_dir(rec.id)
            index = build_frame_index(info, cache)
        except Exception as e:  # noqa: BLE001
            prog.close()
            QMessageBox.critical(None, "영상 분석 실패", f"파일을 읽을 수 없습니다.\n{e}")
            QTimer.singleShot(0, self.show_projects)
            return
        prog.close()
        try:
            project = Project(rec, self.library)
        except RuntimeError as err:
            QMessageBox.warning(None, "프로젝트 열기 실패", str(err))
            QTimer.singleShot(0, self.show_projects)
            return
        res = project.open()
        project.attach_media(info, index)
        # 원본이 바뀐 경우(크기/수정시간/길이) 경고
        summ = None
        if res["restored"]:
            snap, _ = project.store.load_latest()
            summ = (snap or {}).get("media_summary")
        win = EditorWindow(project, self.settings, self.iddict, cache)
        self.window = win
        win.requestProjects.connect(self._back_to_projects)
        win.closed.connect(self._on_window_closed)
        win.show()
        msgs = []
        if res["crashed"] and res["restored"]:
            msgs.append("이전 세션이 비정상 종료되어 마지막으로 정상 저장된 상태를 복구했습니다.")
        if res.get("db_recovered") == "backup":
            msgs.append(
                "프로젝트 저장 파일(DB)이 손상되어 별도 백업에서 복구했습니다. 손상된 파일은 프로젝트 폴더에 "
                "'.corrupt_날짜' 이름으로 보관했습니다. 마지막 편집 내용을 확인하세요."
            )
        elif res.get("db_recovered") == "empty":
            msgs.append(
                "프로젝트 저장 파일(DB)이 손상되었고 복구할 백업도 없어 빈 상태로 시작합니다. "
                "손상된 파일은 프로젝트 폴더에 '.corrupt_날짜' 이름으로 보관했습니다."
            )
        if res["skipped"] and res.get("db_recovered") is None:
            msgs.append(f"손상된 저장본 {res['skipped']}개를 건너뛰고 이전 정상 저장본으로 복구했습니다.")
        if summ and (
            abs(summ.get("duration", 0) - info.duration) > 0.05
            or summ.get("w") != info.width
            or summ.get("fps") != info.fps
        ):
            msgs.append("마스터링 파일이 이전과 다릅니다 (길이/해상도/FPS). 컷·구간 위치를 확인하세요.")
        for w in info.warnings:
            msgs.append(w.message)
        if msgs:
            QTimer.singleShot(400, lambda: QMessageBox.information(win, "프로젝트 열기", "\n\n".join(msgs)))

    def _on_window_closed(self) -> None:
        if not self._switching:  # 사용자가 편집 창을 직접 닫음 -> 프로그램 종료
            self._quit()

    def _back_to_projects(self) -> None:
        w = self.window
        if w is None:
            return
        self._switching = True
        if not w.close():  # 렌더링 중 '취소하고 종료' 를 거절한 경우 등: 그대로 편집을 계속한다
            self._switching = False
            return
        self.window = None
        w.deleteLater()
        QTimer.singleShot(0, self.show_projects)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--selftest":  # 배포본 점검: 창 없이 결과 파일만 쓰고 종료
        from .selftest import run_selftest

        return run_selftest(args[1] if len(args) > 1 else None)
    return App().run()
