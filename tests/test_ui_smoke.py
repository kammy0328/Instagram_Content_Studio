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


@pytest.mark.parametrize("target", ["story", "reels", "cover", "feed_still", "feed_video"])
def test_letterbox_toggle_async_target_and_layout_preservation(qapp, win, monkeypatch, target):
    from ics.core.layout import compute_geometry

    p = win.project
    item = None
    if target.startswith("feed"):
        item = p.add_feed_still(10) if target == "feed_still" else p.add_feed_video(10, 20)[0]
        win.set_tab("feed")
        pump(qapp)
        win.feed_panel.select(item.id)
        editor = win.feed_panel.layout_editor
    else:
        win.set_tab("reels" if target == "cover" else target)
        editor = win.reels_panel.cover_layout if target == "cover" else getattr(win, f"{target}_panel").layout_editor
    pump(qapp)

    def start():
        win.letterbox.busy = True

    monkeypatch.setattr(win.letterbox, "start", start)
    editor.auto_crop.setChecked(True)
    assert win._crop_pending
    # 감지를 기다리는 동안 한 확대 조작은 결과 도착 후에도 유지한다.
    editor.zoom.setValue(125)
    win._layout_commit()
    if item:
        other = p.add_feed_still(40)
        pump(qapp)
        win.feed_panel.select(other.id)
        pump(qapp)
    win.letterbox.busy = False
    win._letterbox_finished((0, 60, 960, 420), "")
    pump(qapp)
    lp = item.layout if item else (p.state.cover.layout if target == "cover" else getattr(p.state, target).layout)
    assert lp.auto_crop and lp.source_crop == (0, 60, 960, 420)
    assert lp.zoom == 1.25
    if item:
        assert not other.layout.auto_crop
        win.feed_panel.select(item.id)
        pump(qapp)
    editor.auto_crop.setChecked(False)
    pump(qapp)
    lp = item.layout if item else (p.state.cover.layout if target == "cover" else getattr(p.state, target).layout)
    assert not lp.auto_crop and lp.source_crop == (0, 60, 960, 420)
    assert compute_geometry(960, 540, "916", lp).source_crop is None
    win.do_undo()
    pump(qapp)
    lp, _ = win._crop_target("feed" if item else target, item.id if item else None)
    assert lp.auto_crop


def test_letterbox_result_cannot_reenable_crop_after_toggle_off(qapp, win, monkeypatch):
    monkeypatch.setattr(win.letterbox, "start", lambda: None)
    editor = win.story_panel.layout_editor
    editor.auto_crop.setChecked(True)
    editor.auto_crop.setChecked(False)
    win._letterbox_finished((0, 60, 960, 420), "")
    pump(qapp)
    assert not win.project.state.story.layout.auto_crop
    assert win.project.state.story.layout.source_crop is None


def test_letterbox_detection_error_leaves_crop_off(qapp, win, monkeypatch):
    monkeypatch.setattr(win.letterbox, "start", lambda: None)
    win.story_panel.layout_editor.auto_crop.setChecked(True)
    win._letterbox_finished(None, "검정 띠 감지 실패: 테스트 오류")
    pump(qapp)
    assert not win.project.state.story.layout.auto_crop
    assert not win._crop_pending


def test_editor_and_credits_fit_laptop_viewport(qapp, win):
    # Windows offscreen 플러그인은 시스템 폰트를 자동 검색하지 못할 수 있다. 실제 한글 글꼴 폭으로 검사한다.
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
    if font.is_file() and not QFontDatabase.families():
        QFontDatabase.addApplicationFont(str(font))
    win.project.update_info(name="아주 긴 프로젝트 이름 " * 12)
    win.resize(1280, 800)
    pump(qapp)
    assert win.width() == 1280
    assert win.height() == 800
    assert win.preview.width() >= 320
    assert win.transport.view_btn.isVisible()
    win.set_tab("credits")
    pump(qapp)
    assert win.width() == 1280
    assert win.credits.body.isVisible()


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


def test_feed_add_still_video_and_drag_reorder(qapp, win):
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
    # 목록에서 끌어서 순서 바꾸기: 두 번째 항목을 맨 앞으로
    lst = win.feed_panel.list
    ids = [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())]
    assert len(ids) == 2
    item = lst.takeItem(1)
    lst.insertItem(0, item)
    lst.model().rowsMoved.emit(lst.rootIndex(), 1, 1, lst.rootIndex(), 0)
    pump(qapp, 200)
    assert [f.id for f in p.feed_ordered()] == [ids[1], ids[0]]
    assert p.state.feed_manual
    # 새로 추가한 항목은 맨 뒤, 순서는 저장/복원 후에도 유지
    win.goto(10)
    win.add_still()
    pump(qapp)
    assert [f.id for f in p.feed_ordered()][:2] == [ids[1], ids[0]]
    from ics.core.model import EditState

    assert [f.id for f in EditState.from_dict(p.state.to_dict()).feed][:2] == [ids[1], ids[0]]
    p.undo()
    pump(qapp)
    assert [f.id for f in p.feed_ordered()] == [ids[1], ids[0]]
    assert not hasattr(win, "carousel") and not hasattr(win.feed_panel, "car_btn")


def test_timeline_has_no_cut_lane_but_scrubbing_the_playhead_still_works(qapp, win):
    from ics.ui import timeline as T

    p, tl = win.project, win.timeline
    p.import_cuts([(0, 40, "a", {}), (40, 90, "b", {}), (90, 150, "c", {}), (150, 240, "d", {})], {})
    pump(qapp)
    tl.fit()
    pump(qapp, 100)
    assert not hasattr(T, "Y_CUT") and not hasattr(tl, "edit_mode") and not hasattr(tl, "cutContextRequested")
    assert T.Y_STORY == T.RULER_H + 4  # 눈금자 바로 아래가 STORY 레인
    before = [(c.start, c.end) for c in p.state.cuts]
    n0 = len(p._undo)
    y = 5
    QTest.mousePress(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(tl.x_of(90)), y))
    for i in range(1, 7):  # 드래그해도 컷 경계는 움직이지 않고 재생 헤드만 따라온다
        QTest.mouseMove(tl, QPoint(int(tl.x_of(90) + (tl.x_of(130) - tl.x_of(90)) * i / 6), y))
        pump(qapp, 10)
    QTest.mouseRelease(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(tl.x_of(130)), y))
    assert abs(win.playhead - 130) <= 2
    assert [(c.start, c.end) for c in p.state.cuts] == before and len(p._undo) == n0
    # 컷 레인이 있던 자리(예전 Y_CUT)를 눌러도 선택/경계 편집은 일어나지 않고 재생 헤드만 이동
    QTest.mouseClick(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(tl.x_of(60)), 15))
    assert abs(win.playhead - 60) <= 2 and win.sel_ids == []


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
    strip, cuts = win.cut_strip, win.project.state.cuts
    _click_tile(qapp, strip, 2)
    assert win.sel_ids == [cuts[2].id] and win.playhead == cuts[2].start
    _click_tile(qapp, strip, 4, Qt.KeyboardModifier.ShiftModifier)  # Shift: 범위 선택, 재생 헤드는 그대로
    assert win.sel_ids == [c.id for c in cuts[2:5]] and win.playhead == cuts[2].start
    strip.set_selected_ids([cuts[0].id, cuts[1].id])
    strip.selectionIdsChanged.emit(strip.selected_ids())
    assert win.sel_ids == [cuts[0].id, cuts[1].id]
    # 병합하면 합쳐진 컷이 양쪽에서 선택된다
    win.merge_selected()
    merged = [c for c in win.project.state.cuts if c.merged][0]
    assert win.sel_ids == [merged.id] and strip.selected_ids() == [merged.id] and strip.count() == len(CUTS) - 1


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


# ---------------------------------------------------------------- 우클릭 메뉴 / 컷 나누기 / 타임라인 정리
def _right_click(widget, pos):
    """위젯(뷰포트)에 오른쪽 클릭의 컨텍스트 메뉴 이벤트를 보낸다."""
    from PySide6.QtGui import QContextMenuEvent

    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, widget.mapToGlobal(pos))
    QApplication.sendEvent(widget, ev)


@pytest.fixture()
def menu_log(win, monkeypatch):
    """메뉴를 화면에 띄우지 않고(블로킹 방지) 메뉴가 떴을 때의 항목 상태를 기록하며, 원하는 항목을 눌러 준다."""
    log = {"shown": [], "pick": None}

    def fake_popup(menu, _pos):
        acts = {x.objectName(): x for x in menu.actions() if x.objectName()}
        log["shown"].append({k: (v.isEnabled(), v.text()) for k, v in acts.items()})
        pick = log["pick"]
        if pick and acts[pick].isEnabled():
            acts[pick].trigger()

    monkeypatch.setattr(win, "_popup", fake_popup)
    return log


def test_right_click_on_shift_selected_cuts_offers_merge_and_merges(qapp, win, menu_log):
    p, strip = win.project, win.cut_strip
    p.import_cuts(CUTS, {})
    pump(qapp, 200)
    _click_tile(qapp, strip, 2)
    _click_tile(qapp, strip, 4, Qt.KeyboardModifier.ShiftModifier)  # #3 ~ #5 선택
    menu_log["pick"] = "merge"
    strip.scrollToItem(strip.item(3))
    pump(qapp, 30)
    _right_click(strip.viewport(), _tile_center(strip, 3))  # 선택 범위 안의 타일을 오른쪽 클릭 -> 선택 유지
    shown = menu_log["shown"][-1]
    assert shown["merge"][0] and "3개 컷 병합" in shown["merge"][1]
    assert not shown["unmerge"][0]
    merged = [c for c in p.state.cuts if c.merged]
    assert len(merged) == 1 and (merged[0].start, merged[0].end) == (CUTS[2][0], CUTS[4][1])
    assert strip.count() == len(CUTS) - 2
    assert win.sel_ids == [merged[0].id]  # 합쳐진 컷이 선택된다


def test_right_click_on_unselected_cut_selects_it_first_and_merge_is_disabled(qapp, win, menu_log):
    win.project.import_cuts(CUTS, {})
    pump(qapp, 200)
    strip, cuts = win.cut_strip, win.project.state.cuts
    _click_tile(qapp, strip, 0)
    _click_tile(qapp, strip, 1, Qt.KeyboardModifier.ShiftModifier)
    assert len(win.sel_ids) == 2
    _right_click(strip.viewport(), _tile_center(strip, 4))  # 선택 밖의 컷
    assert win.sel_ids == [cuts[4].id] and strip.selected_ids() == [cuts[4].id]
    enabled, text = menu_log["shown"][-1]["merge"]
    assert not enabled and "2개 이상" in text
    # 병합된 컷을 오른쪽 클릭하면 '병합 해제' 가 켜진다
    win.sel_ids = [cuts[0].id, cuts[1].id]
    win.merge_selected()
    pump(qapp, 100)
    merged = [c for c in win.project.state.cuts if c.merged][0]
    menu_log["pick"] = "unmerge"
    row = [c.id for c in win.project.state.cuts].index(merged.id)
    _right_click(strip.viewport(), _tile_center(strip, row))
    assert menu_log["shown"][-1]["unmerge"][0]
    assert not any(c.merged for c in win.project.state.cuts)


def test_cut_strip_scrolls_to_center_the_cut_under_the_playhead(qapp, win):
    p, strip = win.project, win.cut_strip
    p.import_cuts([(i * 4, i * 4 + 4, f"c{i}", {}) for i in range(60)], {})
    pump(qapp, 300)
    win.resize(900, 700)
    pump(qapp, 100)
    win.goto(30 * 4 + 1)  # 31번째 컷
    pump(qapp, 100)
    item = strip.item(30)
    r = strip.visualItemRect(item)
    assert abs(r.center().x() - strip.viewport().width() / 2) <= r.width()  # 가운데 근처
    win.goto(5 * 4)
    pump(qapp, 100)
    assert strip.horizontalScrollBar().value() < r.width() * 10  # 앞쪽 컷으로 돌아오면 앞으로 스크롤


def test_context_menu_split_entry_follows_the_playhead_and_feed_tab_has_add_video(qapp, win, menu_log):
    p = win.project
    p.import_cuts(CUTS, {})
    pump(qapp, 200)
    _click_tile(qapp, win.cut_strip, 4)  # 컷 #5 41~150 (재생 헤드는 41 = 컷의 첫 프레임)
    _right_click(win.cut_strip.viewport(), _tile_center(win.cut_strip, 4))
    assert not menu_log["shown"][-1]["split"][0]  # 컷의 첫 프레임에서는 나눌 수 없다
    assert "to_inout" in menu_log["shown"][-1] and "add_video" not in menu_log["shown"][-1]  # STORY 탭
    win.goto(100)
    menu_log["pick"] = "split"
    _right_click(win.cut_strip.viewport(), _tile_center(win.cut_strip, 4))
    assert menu_log["shown"][-1]["split"][0]
    assert [(c.start, c.end) for c in p.state.cuts][4:6] == [(41, 100), (100, 150)]
    win.set_tab("feed")
    menu_log["pick"] = None
    _right_click(win.cut_strip.viewport(), _tile_center(win.cut_strip, 4))
    assert menu_log["shown"][-1]["add_video"][0]


def test_split_at_playhead_button_shortcut_undo_and_selection(qapp, win):
    from PySide6.QtWidgets import QPushButton

    p, strip = win.project, win.cut_strip
    p.import_cuts(CUTS, {})
    pump(qapp, 200)
    assert strip.count() == 6
    _click_tile(qapp, strip, 4)  # #5 선택 (41~150)
    sel_before = win.sel_ids[0]
    win.goto(100)
    btn = next(b for b in win.findChildren(QPushButton) if b.text().startswith("컷 나누기"))
    btn.click()  # 툴바 버튼
    pump(qapp, 100)
    assert [(c.start, c.end) for c in p.state.cuts] == [
        (0, 1),
        (1, 2),
        (2, 40),
        (40, 41),
        (41, 100),
        (100, 150),
        (150, 240),
    ]
    assert strip.count() == 7
    assert win.sel_ids == [p.state.cuts[4].id, p.state.cuts[5].id] and p.state.cuts[4].id == sel_before
    assert strip.selected_ids() == win.sel_ids  # 나눈 컷이 선택돼 있었으면 두 조각이 함께 선택된다
    assert "에서 컷을 나눴습니다" in win.toast_lbl.text()
    # 단축키(C) 액션 - 같은 위치에서 또 나누면 거절(첫 프레임), 상태는 그대로
    win.actions["split_cut"].trigger()
    assert len(p.state.cuts) == 7 and "첫 프레임" in win.toast_lbl.text()
    win.goto(20)
    win.actions["split_cut"].trigger()
    assert len(p.state.cuts) == 8
    win.do_undo()
    win.do_undo()
    assert len(p.state.cuts) == 6


def test_split_works_without_xml_and_in_a_gap(qapp, win):
    p = win.project
    assert p.state.cuts == []
    win.goto(100)
    win.split_at_playhead()
    pump(qapp, 100)
    assert [(c.start, c.end) for c in p.state.cuts] == [(0, 100), (100, p.count)]
    assert win.cut_strip.count() == 2
    p.import_cuts([(0, 50, "a", {}), (80, 120, "b", {})], {})  # 50~80 은 컷 없는 구간
    win.goto(60)
    win.split_at_playhead()
    assert len(p.state.cuts) == 2 and "빈 구간" in win.toast_lbl.text()


def test_timeline_no_longer_has_a_full_length_thumbnail_row(qapp, win):
    from ics.ui import timeline as T

    assert not hasattr(T, "THUMB_H") and not hasattr(T.TimelineWidget, "_paint_thumbs")
    assert not hasattr(T.TimelineWidget, "_paint_cuts") and not hasattr(T.TimelineWidget, "_cut_boundary_at")
    win.project.import_cuts(CUTS, {})
    pump(qapp, 300)
    assert not win.timeline.grab().isNull()
    assert not list(win.thumbs._low), "전체 영상 거친 격자를 더 이상 미리 만들지 않는다"
    # 눈금자를 눌러 이동하는 동작은 그대로
    QTest.mouseClick(win.timeline, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 5))
    assert win.playhead > 0


def test_feed_thumbnails_are_requested_without_the_timeline_thumbnail_row(qapp, win):
    win.set_tab("feed")
    win.goto(70)
    win.add_still()
    t = time.time()
    while win.thumbs.get(70) is None and time.time() - t < 15:
        pump(qapp, 100)
    assert win.thumbs.get(70) is not None
    pump(qapp, 500)
    assert not win.feed_panel.list.item(0).icon().isNull()  # 썸네일이 만들어진 뒤 목록 아이콘이 갱신된다


# ---------------------------------------------------------------- 소리: 끄기 / 장치 문제 알림 / 캐시된 프록시에서 다시 열기
class FakeAudio:
    """실제 오디오 장치 없이 PlaybackEngine 이 소리를 언제 켜고 끄는지만 기록한다."""

    def __init__(self, wav=None):
        self.ok, self.error, self.wav, self.calls = True, "", wav, []

    def play(self, seconds):
        self.calls.append(("play", seconds))

    def stop(self):
        self.calls.append(("stop",))


def _wait(qapp, cond, timeout=30):
    t = time.time()
    while not cond() and time.time() - t < timeout:
        pump(qapp, 100)
    return cond()


def test_mute_button_shortcut_and_setting(qapp, win):
    assert win.transport.mute_btn.isEnabled() and "소리 켜짐" in win.transport.mute_btn.text()
    win.actions["toggle_mute"].trigger()  # Shift+M
    assert not win.playback.audio_enabled and win.settings.get("audio_muted") is True
    assert win.transport.mute_btn.isChecked() and "소리 꺼짐" in win.transport.mute_btn.text()
    win.transport.mute_btn.click()  # 버튼으로 다시 켠다
    assert win.playback.audio_enabled and win.settings.get("audio_muted") is False
    # 저장된 설정은 다음에 창을 열 때 적용된다
    win.settings.set("audio_muted", True)
    win.playback.audio_enabled = True
    win._init_audio_ui()
    assert not win.playback.audio_enabled and win.transport.mute_btn.isChecked()


def test_mute_stops_sound_and_unmute_resumes_at_the_current_position(qapp, win):
    fake = win.playback.audio = FakeAudio()
    win.playback.play(48, 1.0)  # 약 2.0초 지점에서 정배속 재생
    assert fake.calls[-1][0] == "play" and fake.calls[-1][1] == pytest.approx(2.0, abs=0.1)
    win.set_muted(True)
    assert fake.calls[-1] == ("stop",)
    pump(qapp, 400)
    win.set_muted(False)  # 켜면 처음이 아니라 지금 재생 위치부터 소리가 이어진다
    assert fake.calls[-1][0] == "play" and fake.calls[-1][1] > 2.3
    win.playback.pause()
    n = len(fake.calls)
    win.set_muted(True)
    win.set_muted(False)  # 정지 중에는 켜도 소리를 내지 않는다
    assert not [c for c in fake.calls[n:] if c[0] == "play"]
    win.playback.play(10, 2.0)  # 배속 재생은 소리 없음 (기존 동작)
    assert fake.calls[-1] == ("stop",)
    win.playback.pause()


def test_sound_prepared_late_joins_a_running_playback(qapp, win, monkeypatch):
    from ics.ui import services

    monkeypatch.setattr(services, "AudioPlayer", FakeAudio)
    win.playback.play(24, 1.0)
    win.playback.set_audio("late.wav")
    assert win.playback.audio.wav == "late.wav" and win.playback.audio.calls[0][0] == "play"
    win.set_muted(True)
    win.playback.set_audio("later.wav")  # 끈 상태에서는 새로 연결돼도 소리가 나지 않는다
    assert not win.playback.audio.calls
    win.playback.pause()


def test_audio_player_reports_why_there_is_no_sound(qapp, prores_2398, tmp_path, monkeypatch):
    from ics.core import media
    from ics.ui import services
    from ics.ui.services import AudioPlayer, ProxyBuilder

    info = media.analyze(str(prores_2398))
    pb = ProxyBuilder(info, tmp_path / "pc")
    (tmp_path / "pc").mkdir()
    assert pb.build_audio() == "" and pb.audio_path.is_file()
    assert not list((tmp_path / "pc").glob("*.part"))
    assert pb.audio_path.stat().st_size == pytest.approx(info.duration * 48000 * 4, rel=0.1)
    a = AudioPlayer(str(pb.audio_path))
    assert a.ok and a._offset > 0
    a.play(0.0)  # 출력 장치가 없으면(테스트 기본값) 조용히 넘어가지 않고 사유를 남긴다
    assert "출력 장치" in a.error and a._sink is None and a.ok
    a.stop()
    # Qt Multimedia 를 불러올 수 없는 경우(번들 누락 등)도 사유가 남는다
    monkeypatch.setattr(services, "default_audio_output", lambda: (_ for _ in ()).throw(ImportError("QtMultimedia")))
    a.play(0.0)
    assert "소리를 재생하지 못했습니다" in a.error and "QtMultimedia" in a.error
    # 없는 파일 / 깨진 파일
    assert not AudioPlayer(str(tmp_path / "nope.wav")).ok and AudioPlayer(str(tmp_path / "nope.wav")).error
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav at all")
    assert not AudioPlayer(str(bad)).ok and "형식" in AudioPlayer(str(bad)).error
    # 오디오 추출이 실패하면 사유를 돌려주고 찌꺼기(.part)를 남기지 않는다
    import copy

    broken = copy.copy(info)
    broken.path = str(tmp_path / "missing_source.mov")
    pb2 = ProxyBuilder(broken, tmp_path / "pc2")
    (tmp_path / "pc2").mkdir()
    err = pb2.build_audio()
    assert err.startswith("오디오 추출 실패") and not list((tmp_path / "pc2").iterdir())


def test_play_tells_the_user_when_no_output_device_instead_of_staying_silent(qapp, win):
    assert _wait(qapp, lambda: win.playback.audio.ok), win.playback.audio.error  # 재생용 소리 파일이 준비될 때까지
    assert win.transport.audio_lbl.text() == ""
    win.toggle_play()  # 출력 장치 없음 (테스트 기본값)
    assert "출력 장치" in win.toast_lbl.text() and "소리를 낼 수 없음" in win.transport.audio_lbl.text()
    win.toggle_play()
    # 소리를 꺼 둔 상태에서는 장치가 없어도 알리지 않는다
    win.transport.audio_lbl.setText("")
    win.set_muted(True)
    win.toast_lbl.setText("")
    win.toggle_play()
    assert win.toast_lbl.text() in ("", "소리를 껐습니다 (미리보기만 해당)") and win.transport.audio_lbl.text() == ""
    win.toggle_play()


def _open_editor(qapp, lib, rec, prores_2398):
    from ics import paths
    from ics.core import frameindex, media
    from ics.core.iddict import IdDictionary
    from ics.core.project import Project
    from ics.ui.main_window import EditorWindow
    from ics.ui.settings import Settings

    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info, paths.cache_dir(rec.id))
    p = Project(rec, lib)
    p.open()
    p.attach_media(info, idx)
    w = EditorWindow(p, Settings(), IdDictionary(), paths.cache_dir(rec.id))
    w.show()
    return w


def test_sound_and_proxy_are_connected_when_the_proxy_is_already_cached(qapp, prores_2398, tmp_path, monkeypatch):
    """프로젝트를 다시 열면 프록시가 이미 캐시에 있다. 예전에는 이 경로에서 소리 연결과 프록시 색인 로드가 빠져 무음이었다."""
    from ics.core.store import Library

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    lib = Library()
    rec = lib.create("CACHED_PROXY", "A", "T", str(prores_2398), str(tmp_path / "out"))
    w1 = _open_editor(qapp, lib, rec, prores_2398)
    assert _wait(qapp, lambda: w1.decoder.proxy_path is not None and w1.playback.audio.ok)
    assert w1.proxy.ready()
    w1.close()
    pump(qapp, 200)
    w2 = _open_editor(qapp, lib, rec, prores_2398)  # 같은 캐시 폴더로 다시 열기
    try:
        assert _wait(qapp, lambda: w2.decoder.proxy_path is not None), "캐시된 프록시를 쓰지 않음"
        assert _wait(qapp, lambda: w2.playback.audio.ok), "캐시된 프록시인데 소리가 연결되지 않음"
        assert w2.transport.audio_lbl.text() == ""
    finally:
        w2.close()
        pump(qapp, 200)


def test_video_without_audio_disables_the_mute_button(qapp, vfr_h264, tmp_path, monkeypatch):
    from ics.core.store import Library

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    lib = Library()
    rec = lib.create("NO_AUDIO", "A", "T", str(vfr_h264), str(tmp_path / "out"))
    w = _open_editor(qapp, lib, rec, vfr_h264)
    try:
        assert not w.transport.mute_btn.isEnabled() and "소리 없음" in w.transport.mute_btn.text()
        assert w.transport.audio_lbl.text() == ""  # 오디오가 원래 없는 영상은 '소리 준비 중' 같은 문구도 없다
        w.actions["toggle_mute"].trigger()  # 눌러도 아무 일도 없다
        assert w.playback.audio_enabled
    finally:
        w.close()
        pump(qapp, 200)


# ---------------------------------------------------------------- FEED 원본 비율 / Fit 확대
def test_feed_ratio_defaults_to_original_and_can_switch_with_undo(qapp, win):
    p, fp = win.project, win.feed_panel
    assert p.feed_kind() == "orig" and fp.ratio_combo.currentData() == "orig"
    assert "기본" in fp.ratio_combo.itemText(0)
    fp.ratio_combo.setCurrentIndex(1)
    pump(qapp, 50)
    assert p.feed_kind() == "45" and p.state.feed_ratio == "45"
    win.do_undo()
    pump(qapp, 50)
    assert p.feed_kind() == "orig" and fp.ratio_combo.currentData() == "orig"


def test_fit_zoom_slider_is_enabled_and_original_ratio_hides_fit_fill(qapp, win):
    p, fp = win.project, win.feed_panel
    win.set_tab("feed")
    win.goto(40)
    win.add_still()
    pump(qapp, 100)
    le = fp.layout_editor
    assert le.r_fit.isChecked() and le.zoom.isEnabled()  # Fit 에서도 확대 가능
    assert le.r_fit.isHidden() and le.r_fill.isHidden()  # 원본 비율(기본)에서는 Fit/Fill 선택이 없다
    fp.ratio_combo.setCurrentIndex(1)
    pump(qapp, 50)
    assert not le.r_fit.isHidden() and le.zoom.isEnabled()
    le.zoom.setValue(200)
    le.zoom.sliderReleased.emit()
    pump(qapp, 50)
    assert p.state.feed[0].layout.mode == "fit" and p.state.feed[0].layout.zoom == 2.0


def test_planner_uses_original_aspect_for_feed_and_4x5_when_chosen(qapp, win):
    from ics.render.planner import plan_render

    p = win.project
    win.set_tab("feed")
    win.goto(40)
    win.add_still()
    j = [j for j in plan_render(p).jobs if j.kind == "feed_still"][0]
    assert abs(j.params["geo"]["canvas_w"] / j.params["geo"]["canvas_h"] - 960 / 540) < 0.02
    p.set_feed_ratio("45")
    j = [j for j in plan_render(p).jobs if j.kind == "feed_still"][0]
    assert j.params["geo"]["canvas_w"] * 5 == j.params["geo"]["canvas_h"] * 4


def test_reels_cover_ratio_option_defaults_to_original_and_drives_the_plan(qapp, win):
    from ics.render.planner import plan_render

    p, rp = win.project, win.reels_panel
    p.set_range("reels", 10, 60)
    p.set_cover_frame(30)
    pump(qapp, 100)
    assert p.cover_kind() == "orig" and rp.cover_ratio_combo.currentData() == "orig"
    job = [j for j in plan_render(p).jobs if j.kind == "reels_cover"][0]
    g = job.params["geo"]
    assert abs(g["canvas_w"] / g["canvas_h"] - 960 / 540) < 0.02
    rp.cover_ratio_combo.setCurrentIndex(1)
    pump(qapp, 50)
    assert p.state.cover_ratio == "916"
    g = [j for j in plan_render(p).jobs if j.kind == "reels_cover"][0].params["geo"]
    assert g["canvas_w"] * 16 == g["canvas_h"] * 9
    win.do_undo()
    pump(qapp, 50)
    assert p.cover_kind() == "orig" and rp.cover_ratio_combo.currentData() == "orig"


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
    win.open_render({"story", "reels", "feed", "credits"})
    try:
        job = next(j for j in win.render_dialog.plan.jobs if j.kind == "captions")
        assert job.params["caption"] == "렌더 직전 본문"
    finally:
        win.render_dialog.close()


def test_new_project_dialog_needs_only_name_and_master_and_defaults_output_to_master_folder(
    qapp, prores_2398, tmp_path
):
    from ics.ui.dialogs import NewProjectDialog

    d = NewProjectDialog(None, "C:/old_output")
    assert not hasattr(d, "artist") and not hasattr(d, "title")
    d._set_master(str(prores_2398))
    assert d.output.text() == str(prores_2398.parent)  # 마스터링 폴더가 출력 폴더 기본값
    assert d.name.text() == prores_2398.stem
    d.output.setText(str(tmp_path / "custom"))
    d._output_edited = True  # 직접 바꾼 값은 유지
    d._set_master(str(prores_2398))
    assert d.output.text() == str(tmp_path / "custom")
    v = d.values()
    assert v["artist"] == "" and v["title"] == v["name"] and v["output_dir"] == str(tmp_path / "custom")


def test_reverse_play_button_goes_backwards_shows_frames_and_stays_silent(qapp, win):
    fake = win.playback.audio = FakeAudio()
    win.goto(60)
    shown = []
    win.preview.set_image = lambda n, img, _o=win.preview.set_image: (shown.append(n), _o(n, img))
    win.toggle_reverse()
    assert win.playback.playing and win.playback.rate < 0
    pump(qapp, 900)
    moved = win.playhead
    win.toggle_reverse()
    pump(qapp, 150)
    assert moved < 55 and not win.playback.playing
    seq = [n for n in shown if n > 0]  # 처음에 한 번 그려지는 초기 화면(0 프레임)은 제외
    assert len(seq) >= 3 and seq[-1] < seq[0]  # 거꾸로 가는 프레임이 실제로 화면에 나온다
    assert not [c for c in fake.calls if c[0] == "play"]  # 역재생 중에는 노래가 나오지 않는다
