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


# ---------------------------------------------------------------- 컷 스트립 / FEED 영상 구간 / 프로젝트 버튼
CUTS = [(0, 1, "a", {}), (1, 2, "b", {}), (2, 40, "c", {}), (40, 41, "d", {}), (41, 150, "e", {}), (150, 240, "f", {})]


def _tile_center(strip, row):
    return strip.visualItemRect(strip.item(row)).center()


def _click_tile(qapp, strip, row, mods=Qt.KeyboardModifier.NoModifier):
    strip.scrollToItem(strip.item(row))
    pump(qapp, 30)
    QTest.mouseClick(strip.viewport(), Qt.MouseButton.LeftButton, mods, _tile_center(strip, row))
    pump(qapp, 30)


def test_cut_strip_shows_every_cut_at_the_same_size_with_number_and_thumbnail(qapp, win):
    from ics.ui import cutstrip as CS

    win.project.import_cuts(CUTS, {})
    pump(qapp, 200)
    strip = win.cut_strip
    assert strip.count() == len(CUTS)
    # 1프레임 컷도 150프레임 컷도 타일 크기가 같다 (길이에 비례하지 않는다)
    sizes = {
        (strip.visualItemRect(strip.item(i)).width(), strip.visualItemRect(strip.item(i)).height()) for i in range(6)
    }
    assert sizes == {(CS.TILE_W, CS.TILE_H)}
    assert [strip.item(i).data(CS.DATA)["num"] for i in range(6)] == [1, 2, 3, 4, 5, 6]
    assert not strip.grab().isNull()  # 델리게이트 그리기 오류 없음
    # 컷을 알아볼 수 있게 썸네일이 만들어진다 (1프레임 컷 #1 의 대표 프레임 = 0)
    t = time.time()
    while win.cut_thumbs.get(0) is None and time.time() - t < 10:
        pump(qapp, 100)
    assert win.cut_thumbs.get(0) is not None


def test_cut_strip_selection_syncs_with_timeline_and_moves_playhead(qapp, win):
    win.project.import_cuts(CUTS, {})
    pump(qapp, 200)
    strip, tl, cuts = win.cut_strip, win.timeline, win.project.state.cuts
    _click_tile(qapp, strip, 2)
    assert tl.sel_ids == [cuts[2].id] and win.playhead == cuts[2].start
    _click_tile(qapp, strip, 4, Qt.KeyboardModifier.ShiftModifier)  # Shift: 범위 선택, 재생 헤드는 그대로
    assert tl.sel_ids == [c.id for c in cuts[2:5]] and win.playhead == cuts[2].start
    # 타임라인에서 바꾸면 스트립에도 반영
    tl.sel_ids = [cuts[0].id, cuts[1].id]
    tl.cutSelectionChanged.emit(list(tl.sel_ids))
    assert strip.selected_ids() == [cuts[0].id, cuts[1].id]
    # 병합하면 합쳐진 컷이 양쪽에서 선택된다
    win.merge_selected()
    merged = [c for c in win.project.state.cuts if c.merged][0]
    assert tl.sel_ids == [merged.id] and strip.selected_ids() == [merged.id] and strip.count() == len(CUTS) - 1


def test_feed_add_video_uses_exactly_the_selected_cuts_not_a_stale_in_out(qapp, win):
    p = win.project
    p.import_cuts(CUTS, {})
    pump(qapp, 200)
    win.set_tab("feed")
    cuts = p.state.cuts
    p.set_ui("feed_in", 0)  # 예전에 지정해 둔 In/Out (이전에 컷 대신 이 값이 우선해서 1:07:00 까지 들어갔다)
    p.set_ui("feed_out", 239)
    _click_tile(qapp, win.cut_strip, 2)
    assert (win.feed_panel._in, win.feed_panel._out) == (cuts[2].start, cuts[2].end)  # 패널에도 같은 구간이 보인다
    win.add_video()
    it = p.state.feed[-1]
    assert (it.kind, it.start, it.end) == ("video", cuts[2].start, cuts[2].end)
    # Shift 로 여러 컷 선택: 선택한 구간 끝까지만
    _click_tile(qapp, win.cut_strip, 3)
    _click_tile(qapp, win.cut_strip, 4, Qt.KeyboardModifier.ShiftModifier)
    win.add_video()
    it = p.state.feed[-1]
    assert (it.start, it.end) == (cuts[3].start, cuts[4].end)
    assert len(p.state.feed) == 2


def test_feed_in_out_keys_adjust_the_selected_cut_range(qapp, win):
    p = win.project
    p.import_cuts(CUTS, {})
    pump(qapp, 200)
    win.set_tab("feed")
    cuts = p.state.cuts
    _click_tile(qapp, win.cut_strip, 4)  # 컷 #5: 41~150
    win.goto(100)
    win.set_out()  # Out 만 직접 지정 -> In 은 선택한 컷의 시작을 유지
    win.add_video()
    it = p.state.feed[-1]
    assert (it.start, it.end) == (cuts[4].start, 101)  # Out 은 현재 프레임 포함
    # In/Out 을 직접 지정한 뒤 컷을 새로 선택하면 컷이 우선한다
    win.goto(10)
    win.set_in()
    _click_tile(qapp, win.cut_strip, 5)
    win.add_video()
    assert (p.state.feed[-1].start, p.state.feed[-1].end) == (cuts[5].start, cuts[5].end)


def _app_with_project(qapp, prores_2398, tmp_path, monkeypatch):
    from ics.app import App

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    app = App()
    calls = {"list": 0, "quit": 0}
    monkeypatch.setattr(app, "show_projects", lambda: calls.__setitem__("list", calls["list"] + 1))
    monkeypatch.setattr(app, "_quit", lambda: calls.__setitem__("quit", calls["quit"] + 1))
    rec = app.library.create("APP_TEST", "A", "T", str(prores_2398), str(tmp_path / "out"))
    app.open_record(rec)
    assert app.window is not None
    return app, calls


def test_projects_button_goes_back_to_the_list_instead_of_quitting(qapp, prores_2398, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer

    app, calls = _app_with_project(qapp, prores_2398, tmp_path, monkeypatch)
    win, state = app.window, {"end": False}
    QTimer.singleShot(200, win.requestProjects.emit)  # 편집 창의 '← 프로젝트' 버튼
    QTimer.singleShot(1500, lambda: (state.update(end=True), qapp.quit()))
    qapp.exec()  # 이전에는 창이 닫히는 순간 마지막 창이라 여기서 바로 끝났다
    assert state["end"], "프로젝트 버튼을 누르자 앱 이벤트 루프가 종료됨"
    assert calls == {"list": 1, "quit": 0} and app.window is None


def test_closing_the_editor_window_still_quits_the_app(qapp, prores_2398, tmp_path, monkeypatch):
    app, calls = _app_with_project(qapp, prores_2398, tmp_path, monkeypatch)
    app.window.close()
    pump(qapp, 100)
    assert calls == {"list": 0, "quit": 1}
