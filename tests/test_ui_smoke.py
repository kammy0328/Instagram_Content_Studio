"""오프스크린 Qt 로 편집 창의 핵심 동작(탭/In-Out/피드/타임라인 컷 편집/Undo/렌더 큐 연동)을 검증한다."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp, prores_2398, tmp_path, monkeypatch):
    from ics import paths
    from ics.core import frameindex, media
    from ics.core.iddict import IdDictionary
    from ics.core.project import Project
    from ics.core.store import Library
    from ics.ui.main_window import EditorWindow
    from ics.ui.settings import Settings

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    lib = Library()
    rec = lib.create("UI_TEST", "A", "T", str(prores_2398), str(tmp_path / "out"))
    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info, paths.cache_dir(rec.id))
    p = Project(rec, lib)
    p.open()
    p.attach_media(info, idx)
    w = EditorWindow(p, Settings(), IdDictionary(), paths.cache_dir(rec.id))
    w.resize(1500, 900)
    w.show()
    pump(qapp, 400)
    yield w
    w.close()
    pump(qapp, 200)


def pump(app, ms=200):
    t = time.time()
    while time.time() - t < ms / 1000:
        app.processEvents()
        time.sleep(0.005)


def test_tabs_in_out_inclusive_and_undo(qapp, win):
    p = win.project
    win.goto(50)
    QTest.keyClick(win, Qt.Key.Key_I)
    for _ in range(10):
        QTest.keyClick(win, Qt.Key.Key_Right)
    QTest.keyClick(win, Qt.Key.Key_O)
    pump(qapp)
    assert (p.state.story.start, p.state.story.end) == (50, 61)  # Out 은 현재 프레임 포함
    win.set_tab("reels")
    win.goto(100)
    win.apply_preset("reels", "30")
    pump(qapp)
    assert p.state.reels.enabled and p.state.reels.start == 100
    win.do_undo()
    pump(qapp)
    assert not p.state.reels.enabled
    win.do_redo()
    pump(qapp)
    assert p.state.reels.enabled
    for tab in ("feed", "credits", "story"):
        win.set_tab(tab)
        pump(qapp, 50)
    assert win.tab == "story"


def test_feed_add_still_video_and_carousel(qapp, win):
    p = win.project
    win.set_tab("feed")
    win.goto(30)
    win.add_still()
    win.goto(60)
    QTest.keyClick(win, Qt.Key.Key_I)
    win.goto(90)
    QTest.keyClick(win, Qt.Key.Key_O)
    win.add_video()
    pump(qapp)
    assert [f.kind for f in p.feed_included()] == ["still", "video"]
    assert (p.feed_included()[1].start, p.feed_included()[1].end) == (60, 91)
    win.feed_panel.car_btn.setChecked(True)
    pump(qapp, 600)
    assert win.center_stack.currentIndex() == 1 and len(win.carousel.items) == 2
    win.carousel.show_index(1)
    win.feed_panel.car_btn.setChecked(False)
    assert win.center_stack.currentIndex() == 0


def test_timeline_cut_selection_merge_and_boundary_drag(qapp, win):
    from ics.ui import timeline as T

    p, tl = win.project, win.timeline
    p.import_cuts([(0, 40, "a", {}), (40, 90, "b", {}), (90, 150, "c", {}), (150, 240, "d", {})], {})
    pump(qapp)
    tl.fit()
    pump(qapp, 100)
    y = T.Y_CUT + 12

    def click(frame, mods=Qt.KeyboardModifier.NoModifier):
        QTest.mouseClick(tl, Qt.MouseButton.LeftButton, mods, QPoint(int(tl.x_of(frame)), y))
        pump(qapp, 30)

    click(60)
    click(120, Qt.KeyboardModifier.ShiftModifier)
    assert len(tl.sel_ids) == 2
    win.merge_selected()
    assert [(c.start, c.end, c.merged) for c in p.state.cuts] == [(0, 40, False), (40, 150, True), (150, 240, False)]
    win.do_undo()
    assert len(p.state.cuts) == 4
    tl.snap_on = False
    n0 = len(p._undo)
    QTest.mousePress(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(tl.x_of(90)), y))
    for i in range(1, 7):
        QTest.mouseMove(tl, QPoint(int(tl.x_of(90) + (tl.x_of(110) - tl.x_of(90)) * i / 6), y))
        pump(qapp, 10)
    QTest.mouseRelease(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(tl.x_of(110)), y))
    assert [(c.start, c.end) for c in p.state.cuts][1:3] == [(40, 110), (110, 150)]  # 롤: 두 경계 함께 이동
    assert len(p._undo) == n0 + 1  # 드래그 전체가 Undo 1회


def test_playback_advances_and_pauses(qapp, win):
    win.goto(5)
    win.toggle_play()
    pump(qapp, 900)
    moved = win.playhead
    win.toggle_play()
    pump(qapp, 150)
    assert moved > 10 and not win.playback.playing


def test_render_through_window(qapp, win, tmp_path):
    from ics.render.planner import plan_render

    p = win.project
    p.set_range("story", 10, 34)
    win.set_tab("feed")
    win.goto(40)
    win.add_still()
    p.set_caption_body("테스트")
    plan = plan_render(p)
    assert not plan.blocked
    win._start_render(plan, True)
    t0 = time.time()
    while win.render_queue.running and time.time() - t0 < 120:
        pump(qapp, 100)
    pump(qapp, 200)
    assert all(j.status == "done" for j in plan.jobs), [(j.label, j.status, j.error) for j in plan.jobs]
    assert "렌더링 완료" in win.render_lbl.text()


def test_close_saves_pending_credit_text(qapp, win):
    win.set_tab("credits")
    win.credits.raw.setPlainText("Director | 닫기 직전 원문")
    win.credits.body.setPlainText("닫기 직전 본문")
    assert win.close()
    from ics.core.store import ProjectStore

    store = ProjectStore(win.project.store.path)
    try:
        state = store.load_latest()[0]["state"]
        assert state["credits_raw"] == "Director | 닫기 직전 원문"
        assert state["caption_body"] == "닫기 직전 본문"
    finally:
        store.close()


def test_close_failure_keeps_editor_open(qapp, win, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: errors.append(args))
    save = win.project.store.save

    def full_disk(payload):
        raise OSError(28, "디스크 공간 부족")

    monkeypatch.setattr(win.project.store, "save", full_disk)
    win.project.set_caption_body("저장 실패")
    assert not win.close()
    assert errors and not win._closing and not win.project._closed
    monkeypatch.setattr(win.project.store, "save", save)


def test_back_to_projects_respects_cancelled_close():
    from ics.app import App

    class Window:
        deleted = False

        def close(self):
            return False

        def deleteLater(self):
            self.deleted = True

    app = App.__new__(App)
    app.window = Window()
    window = app.window
    app._back_to_projects()
    assert app.window is window and not window.deleted


def test_render_plan_includes_pending_caption(qapp, win):
    win.project.set_range("story", 0, 4)
    win.set_tab("credits")
    win.credits.body.setPlainText("렌더 직전 본문")
    win.open_render()
    try:
        job = next(j for j in win.render_dialog.plan.jobs if j.kind == "captions")
        assert job.params["caption"] == "렌더 직전 본문"
    finally:
        win.render_dialog.close()
