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

from ics.core.layout import LayoutParams  # noqa: E402


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
    # 감지를 기다리는 동안 한 위치 조작은 결과 도착 후에도 유지한다.
    editor.py.setValue(30)
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
    assert abs(lp.pos_y - 0.3) < 1e-9
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
    win.apply_preset("story", "15")
    pump(qapp)
    assert p.state.story.enabled and p.state.story.start == 50
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
    # 컷 레인이 있던 자리(예전 Y_CUT)를 눌러도 경계 편집은 일어나지 않고 재생 헤드만 이동 (컷 선택은 재생 헤드를 따라감)
    QTest.mouseClick(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(tl.x_of(60)), 15))
    assert abs(win.playhead - 60) <= 2 and win.sel_ids == [p.state.cuts[1].id]
    assert [(c.start, c.end) for c in p.state.cuts] == before


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
    pump(qapp, 450)  # 부드럽게 미끄러져 가운데로 (애니메이션)
    item = strip.item(30)
    r = strip.visualItemRect(item)
    assert abs(r.center().x() - strip.viewport().width() / 2) <= r.width()  # 가운데 근처
    win.goto(5 * 4)
    pump(qapp, 450)
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
    assert win.transport.mute_btn.isEnabled() and win.transport.mute_btn.accessibleName() == "소리 켜짐"
    win.actions["toggle_mute"].trigger()  # Shift+M
    assert not win.playback.audio_enabled and win.settings.get("audio_muted") is True
    assert win.transport.mute_btn.isChecked() and win.transport.mute_btn.accessibleName() == "소리 꺼짐"
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
        assert not w.transport.mute_btn.isEnabled() and "오디오 트랙이 없습니다" in w.transport.mute_btn.toolTip()
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


def test_fit_position_slider_is_enabled_and_original_ratio_hides_fit_fill(qapp, win):
    p, fp = win.project, win.feed_panel
    win.set_tab("feed")
    win.goto(40)
    win.add_still()
    pump(qapp, 100)
    le = fp.layout_editor
    assert le.r_fit.isChecked() and le.py.isEnabled()
    assert le.r_fit.isHidden() and le.r_fill.isHidden()  # 원본 비율(기본)에서는 Fit/Fill 선택이 없다
    fp.ratio_combo.setCurrentIndex(1)
    pump(qapp, 50)
    assert not le.r_fit.isHidden() and le.py.isEnabled()
    le.py.setValue(20)
    le.py.sliderReleased.emit()
    pump(qapp, 50)
    assert p.state.feed[0].layout.mode == "fit" and abs(p.state.feed[0].layout.pos_y - 0.2) < 1e-9


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


def test_slider_wheel_blocked_and_double_click_resets(qapp):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    from ics.ui.panels import ResetSlider

    s = ResetSlider(0, 100, 50)
    s.setValue(30)
    ev = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    qapp.sendEvent(s, ev)
    assert s.value() == 30
    QTest.mouseDClick(s, Qt.MouseButton.LeftButton)
    assert s.value() == 50


def test_cover_preview_follows_playhead_not_the_pinned_cover_frame(qapp, win):
    win.set_tab("reels")
    win.goto(10)
    win.project.set_cover_frame(10)
    win.reels_panel.cover_prev.setChecked(True)
    pump(qapp, 50)
    assert win.cover_mode and win._display_frame() == 10
    win.goto(40)
    pump(qapp, 50)
    assert win._display_frame() == 40  # 지정된 커버(10)가 아니라 현재 위치를 미리 본다


def test_xml_import_cuts_immediately_without_dialog(qapp, win):
    from pathlib import Path

    from ics.ui import dialogs

    def boom(*a, **k):
        raise AssertionError("XML 불러오기에서 창이 뜨면 안 됩니다")

    orig = dialogs.XmlImportDialog.exec
    dialogs.XmlImportDialog.exec = boom
    try:
        xml = Path(__file__).parent / "data" / "sample_premiere.xml"
        assert win.import_xml_file(str(xml))
        pump(qapp, 50)
        assert [(c.start, c.end) for c in win.project.state.cuts] == [(0, 50), (50, 120), (120, 200), (200, 240)]
        cs = win.project.state.cuts
        win.project.merge_cuts([cs[0].id, cs[1].id])  # 수동 편집이 있어도 확인 창 없이 덮어쓴다
        assert len(win.project.state.cuts) == 3
        assert win.import_xml_file(str(xml))
        assert len(win.project.state.cuts) == 4
        win.do_undo()  # 덮어쓴 것은 실행 취소로 되돌린다
        assert len(win.project.state.cuts) == 3
    finally:
        dialogs.XmlImportDialog.exec = orig


def test_dropping_an_xml_on_the_window_cuts(qapp, win):
    from pathlib import Path

    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDropEvent

    xml = Path(__file__).parent / "data" / "sample_premiere.xml"
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(xml))])
    ev = QDropEvent(
        QPointF(10, 10), Qt.DropAction.CopyAction, md, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
    )
    win.dropEvent(ev)
    pump(qapp, 100)
    assert len(win.project.state.cuts) == 4
    other = QMimeData()
    other.setUrls([QUrl.fromLocalFile(str(xml.with_suffix(".txt")))])
    assert win._xml_from_mime(other) is None


def test_workspace_tabs_live_in_the_top_bar(qapp, win):
    assert set(win.nav_btns) == {"story", "reels", "feed", "credits"}
    win.nav_btns["credits"].click()
    pump(qapp, 50)
    assert win.pages.currentIndex() == 1 and win.nav_btns["credits"].isChecked()
    win.nav_btns["story"].click()
    pump(qapp, 50)
    assert win.pages.currentIndex() == 0
    assert win.findChild(type(win.nav_btns["story"].parent()), "sidebar") is None


def test_volume_slider_sets_preview_volume_and_is_saved(qapp, win):
    win.transport.volume.setValue(40)
    assert win.settings.get("audio_volume") == 40 and abs(win.playback.audio.volume - 0.4) < 1e-9
    win.playback.set_audio(None)  # 소리 파일을 다시 붙여도 음량은 유지된다
    assert abs(win.playback.audio.volume - 0.4) < 1e-9
    QTest.mouseDClick(win.transport.volume, Qt.MouseButton.LeftButton)  # 더블클릭: 100%
    assert win.transport.volume.value() == 100 and win.settings.get("audio_volume") == 100
    win.transport.mute_btn.click()
    assert not win.transport.volume.isEnabled()  # 소리를 끄면 음량 슬라이더도 비활성
    win.transport.mute_btn.click()


def test_tabs_use_indicators_not_check_marks_and_toolbar_labels_are_plain(qapp, win):
    for b in win.nav_btns.values():
        assert "✓" not in b.text()
    win.apply_preset("story", "15")
    win._refresh()
    pump(qapp, 50)
    assert win.nav_btns["story"].done
    texts = [b.text() for b in win.findChildren(type(win.snap_btn))]
    assert "병합" in texts and "컷 나누기" in texts and "스냅" in texts
    assert not any(t.endswith(("(M)", "(C)")) or "✓" in t or "→ 구간" in t for t in texts)
    assert win.snap_btn.isChecked()
    win.snap_btn.click()
    assert not win.timeline.snap_on and not win.snap_btn.isChecked()
    win.snap_btn.click()
    assert "미리보기 준비됨" not in win.proxy_lbl.text()


def test_select_cuts_chip_sets_the_range(qapp, win):
    from pathlib import Path

    assert not win.story_panel.cuts_btn.isEnabled()
    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    cs = win.project.state.cuts
    win._set_cut_selection([cs[1].id, cs[2].id])
    assert win.story_panel.cuts_btn.isEnabled()
    win.set_tab("story")
    win.story_panel.cuts_btn.click()
    st = win.project.state.story
    assert (st.start, st.end) == (cs[1].start, cs[2].end)


def test_auto_reframe_sets_per_cut_positions_and_preview_follows(qapp, win, monkeypatch):
    from pathlib import Path

    from ics.core import reframe

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.set_tab("story")
    win.goto(0)
    win.project.set_range("story", 0, 240)
    win.project.set_selection_layout("story", LayoutParams("fit"))
    monkeypatch.setattr(reframe, "analyze", lambda *a, **k: {0: 0.0, 50: 1.0})
    win.story_panel.layout_editor.reframe_btn.click()
    assert _wait(qapp, lambda: win.project.state.story.cut_x == {0: 0.0, 50: 1.0}, 10)
    assert win.project.state.story.layout.mode == "fill"  # 자동 맞춤은 Fill 9:16 에서 동작
    win.goto(10)
    pump(qapp, 300)
    assert win.preview.layout.pos_x == 0.0
    win.goto(60)
    pump(qapp, 300)
    assert win.preview.layout.pos_x == 1.0
    win.do_undo()  # 실행 취소로 되돌린다
    assert win.project.state.story.cut_x == {}


def test_preview_drag_moves_only_the_current_cut_horizontally(qapp, win):
    from pathlib import Path

    from PySide6.QtCore import QPoint

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.set_tab("reels")
    win.project.set_range("reels", 0, 240)
    win.project.set_selection_layout("reels", LayoutParams("fill", pos_y=0.5))
    win.goto(60)  # 두 번째 컷 (50~120)
    pump(qapp, 300)
    pv = win.preview
    c = pv.rect().center()
    QTest.mousePress(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    QTest.mouseMove(pv, c + QPoint(-60, 40))
    QTest.mouseRelease(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c + QPoint(-60, 40))
    pump(qapp, 50)
    sel = win.project.state.reels
    assert set(sel.cut_x) == {50} and sel.cut_x[50] > 0.5  # 왼쪽으로 끌면 화면이 오른쪽 부분을 보인다
    assert sel.layout.pos_y == 0.5 and sel.layout.zoom == 1.0  # 세로/확대는 그대로
    win.reels_panel.layout_editor.reframe_reset.click()
    assert win.project.state.reels.cut_x == {}


def test_preview_drag_does_nothing_in_fit(qapp, win):
    from PySide6.QtCore import QPoint

    win.set_tab("story")
    win.project.set_range("story", 0, 100)
    win.project.set_selection_layout("story", LayoutParams("fit"))
    pump(qapp, 100)
    pv = win.preview
    c = pv.rect().center()
    QTest.mousePress(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    QTest.mouseMove(pv, c + QPoint(-80, 0))
    QTest.mouseRelease(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c + QPoint(-80, 0))
    assert win.project.state.story.cut_x == {}


def test_reframe_single_cut_and_markers(qapp, win, monkeypatch):
    from pathlib import Path

    from ics.core import reframe

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.set_tab("reels")
    win.project.set_range("reels", 0, 240)
    win.project.set_selection_layout("reels", LayoutParams("fill"))
    seen = []

    def fake(info, index, layout, segs, *a, **k):
        seen.append([s.key for s in segs])
        return {s.key: 0.9 for s in segs if s.key != 120}  # 세 번째 컷에는 인물 없음

    monkeypatch.setattr(reframe, "analyze", fake)
    # 전체 컷
    win.reels_panel.layout_editor.reframe_btn.click()
    assert _wait(qapp, lambda: win.project.state.reels.cut_x_src, 10)
    sel = win.project.state.reels
    assert seen[-1] == [0, 50, 120, 200]  # 영상 전체 컷
    assert sel.cut_x_src == {0: "auto", 50: "auto", 120: "none", 200: "auto"} and 120 not in sel.cut_x
    # 컷 스트립 표시: 9:16 창 위치와 출처
    src, xf, wf = win._cut_reframe_info(0, 50)
    assert src == "auto" and 0 < wf < 1 and xf > 0.5
    assert win._cut_reframe_info(120, 200)[0] == "none"
    # 지금 컷만 (선택 없음 -> 재생 헤드가 있는 컷)
    win.project.clear_cut_x("reels")
    win.goto(60)
    win.reels_panel.layout_editor.reframe_some_btn.click()
    assert _wait(qapp, lambda: win.project.state.reels.cut_x_src, 10)
    assert seen[-1] == [50] and win.project.state.reels.cut_x_src == {50: "auto"}
    # 선택 컷 2개만
    cs = win.project.state.cuts
    win._set_cut_selection([cs[2].id, cs[3].id])
    assert win.reels_panel.layout_editor.reframe_some_btn.text() == "선택 컷 2"
    win.reels_panel.layout_editor.reframe_some_btn.click()
    assert _wait(qapp, lambda: 200 in win.project.state.reels.cut_x_src, 10)
    assert seen[-1] == [120, 200]
    assert win.project.state.reels.cut_x_src == {50: "auto", 120: "none", 200: "auto"}  # 앞서 맞춘 컷은 그대로
    # 드래그(수동)는 '수동' 으로 표시
    win.project.set_cut_x("reels", {0: 0.1}, src="manual")
    assert win._cut_reframe_info(0, 50)[0] == "manual"
    # STORY 탭/피드에는 표시하지 않는다
    win.set_tab("feed")
    assert win._cut_reframe_info(0, 50) is None
    win.cut_strip.grab()  # 타일 그리기에서 오류가 나지 않는다


def test_cut_selection_follows_the_playhead_and_strip_centers_it(qapp, win):
    from pathlib import Path

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.set_tab("story")
    cs = win.project.state.cuts
    win.goto(10)
    assert win.sel_ids == [cs[0].id]
    for _ in range(45):  # 방향키로 컷 경계를 넘는다
        win.actions["step_fwd"].trigger()
    assert win.playhead >= 50 and win.sel_ids == [cs[1].id]
    assert win.cut_strip.selected_ids() == [cs[1].id] and win.cut_strip.current_id == cs[1].id
    # 여러 컷을 고른 중이면 선택을 유지한다
    win._set_cut_selection([cs[1].id, cs[2].id])
    win.goto(210)
    assert win.sel_ids == [cs[1].id, cs[2].id]
    assert win.cut_strip.focusPolicy() == Qt.FocusPolicy.NoFocus  # 방향키는 프레임 이동용


def test_current_cut_tile_shows_the_playhead_inside_the_cut(qapp, win):
    from pathlib import Path

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.goto(85)  # 두 번째 컷 (50~120) 의 중간
    pump(qapp, 100)
    assert win.cut_strip.playhead == 85 and win.cut_strip.current_id == win.project.state.cuts[1].id
    img = win.cut_strip.grab().toImage()
    assert not img.isNull()


def test_auto_reframe_covers_cuts_outside_the_range(qapp, win, monkeypatch):
    from pathlib import Path

    from ics.core import reframe

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.set_tab("story")
    win.project.set_range("story", 60, 100)  # 두 번째 컷 일부만 구간
    seen = []
    monkeypatch.setattr(reframe, "analyze", lambda i, x, lp, segs, *a, **k: seen.append([s.key for s in segs]) or {})
    win.story_panel.layout_editor.reframe_btn.click()
    assert _wait(qapp, lambda: seen, 10)
    assert seen[-1] == [0, 50, 120, 200]
    assert win._cut_reframe_info(200, 240) is not None  # 구간 밖 컷도 컷 목록에 표시


def test_reframe_after_xml_option_runs_once_for_both(qapp, win, monkeypatch):
    from pathlib import Path

    from ics.core import reframe

    runs = []
    monkeypatch.setattr(reframe, "analyze", lambda i, x, lp, segs, *a, **k: runs.append(1) or {0: 0.2})
    win.project.set_selection_layout("reels", LayoutParams("fill"))
    xml = str(Path(__file__).parent / "data" / "sample_premiere.xml")
    assert not win.reframe_after_xml_action.isChecked()
    win.import_xml_file(xml)
    pump(qapp, 200)
    assert runs == []  # 기본은 꺼짐
    win.reframe_after_xml_action.setChecked(True)
    assert win.settings.get("reframe_after_xml") is True
    win.import_xml_file(xml)
    assert _wait(qapp, lambda: len(runs) == 1 and win.project.state.reels.cut_x, 10)
    assert win.project.state.story.cut_x == {0: 0.2} and win.project.state.reels.cut_x == {0: 0.2}
    win.reframe_after_xml_action.setChecked(False)


def test_reframe_can_be_cancelled_and_uses_the_proxy(qapp, win, monkeypatch):
    import threading

    from ics.core import reframe

    gate = threading.Event()

    def slow(info, index, layout, segs, decode, cancel, progress, **k):
        while not cancel.is_set():
            gate.wait(0.02)
        raise InterruptedError

    monkeypatch.setattr(reframe, "analyze", slow)
    win.set_tab("story")
    le = win.story_panel.layout_editor
    le.reframe_btn.click()
    pump(qapp, 50)
    assert le.reframe_cancel.isVisible() and not le.reframe_btn.isEnabled()
    le.reframe_cancel.click()
    assert _wait(qapp, lambda: le.reframe_btn.isEnabled(), 10)
    assert not le.reframe_cancel.isVisible() and win.project.state.story.cut_x == {}
    # 프록시가 준비되면 분석도 프록시를 읽는다 (프레임 수가 같을 때만)
    assert _wait(qapp, lambda: win.proxy.index is not None, 60)
    pump(qapp, 200)
    assert win.reframer.proxy_path == str(win.proxy.video_path)


def test_overlay_sections_collapse_and_remember(qapp, win):
    oe = win.story_panel.overlay_editor
    assert oe.sec_title.body.isVisibleTo(oe) and not oe.sec_look.body.isVisibleTo(oe)  # '표시' 는 접힌 채 시작
    oe.sec_title.head.click()
    assert not oe.sec_title.is_open() and win.settings.get("overlay_section_title") is False  # 상태는 즉시
    pump(qapp, 400)  # 접히는 애니메이션이 끝나면 내용이 숨는다
    assert not oe.sec_title.body.isVisibleTo(oe)
    oe.sec_title.head.click()
    assert oe.sec_title.body.isVisibleTo(oe)
    pump(qapp, 400)
    assert oe.sec_title.body.maximumHeight() > 10000  # 펼친 뒤에는 높이 제한이 풀린다


def test_editor_window_ticks_progress_while_building(qapp, prores_2398, tmp_path):
    from ics import paths
    from ics.core import frameindex, media
    from ics.core.iddict import IdDictionary
    from ics.core.project import Project
    from ics.core.store import Library
    from ics.ui.main_window import EditorWindow
    from ics.ui.settings import Settings

    lib = Library()
    rec = lib.create("TICK", "A", "T", str(prores_2398), str(tmp_path / "out"))
    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info, paths.cache_dir(rec.id))
    p = Project(rec, lib)
    p.open()
    p.attach_media(info, idx)
    ticks = []
    w = EditorWindow(p, Settings(), IdDictionary(), paths.cache_dir(rec.id), progress=lambda: ticks.append(1))
    try:
        assert len(ticks) >= 4  # 단계 사이마다 로딩 화면을 다시 그릴 기회가 있다
    finally:
        w.close()
        pump(qapp, 100)


def test_cut_scrubber_scrubs_only_inside_the_selected_cut(qapp, win):
    from pathlib import Path

    from PySide6.QtCore import QPoint

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.goto(60)  # 두 번째 컷 50~120
    pump(qapp, 50)
    sc = win.cut_scrub
    assert (sc.start, sc.end, sc.label) == (50, 120, "#2")
    b = sc._area()
    y = int(b.center().y())
    # 맨 왼쪽 밖에서 시작해 맨 오른쪽 밖까지 끌어도 그 컷의 처음~끝에서만 움직인다
    QTest.mousePress(sc, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(2, y))
    assert win.playhead == 50
    QTest.mouseMove(sc, QPoint(int(b.center().x()), y))
    assert 80 <= win.playhead <= 90
    QTest.mouseMove(sc, QPoint(sc.width() - 2, y))
    assert win.playhead == 119 and (sc.start, sc.end) == (50, 120)  # 다음 컷으로 넘어가지 않는다
    QTest.mouseRelease(sc, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(sc.width() - 2, y))
    assert win.sel_ids == [win.project.state.cuts[1].id]
    # 여러 컷을 고르면 고른 컷들의 처음~끝
    cs = win.project.state.cuts
    win._set_cut_selection([cs[1].id, cs[2].id])
    assert (sc.start, sc.end, sc.label) == (50, 200, "#2–3")
    sc.grab()


def test_story_and_reels_share_the_cut_positions(qapp, win, monkeypatch):
    from pathlib import Path

    from ics.core import reframe

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    win.project.set_selection_layout("story", LayoutParams("fill"))
    win.project.set_selection_layout("reels", LayoutParams("fill"))
    monkeypatch.setattr(reframe, "analyze", lambda *a, **k: {0: 0.1, 50: 0.9})
    win.set_tab("story")
    win.story_panel.layout_editor.reframe_btn.click()
    assert _wait(qapp, lambda: win.project.state.story.cut_x, 10)
    st, rl = win.project.state.story, win.project.state.reels
    assert rl.cut_x == st.cut_x == {0: 0.1, 50: 0.9} and rl.cut_x_src == st.cut_x_src
    win.set_tab("reels")
    assert win._cut_reframe_info(0, 50)[0] == "auto"  # REELS 컷 목록에도 '자동' 표시
    win.project.set_cut_x("reels", {50: 0.4})  # REELS 에서 드래그해도 STORY 에 반영
    assert win.project.state.story.cut_x[50] == 0.4 and win.project.state.story.cut_x_src[50] == "manual"
    win.project.clear_cut_x("story")
    assert win.project.state.reels.cut_x == {}
    win.do_undo()  # 한 번에 되돌림
    assert win.project.state.story.cut_x[50] == 0.4 and win.project.state.reels.cut_x[50] == 0.4


def test_cut_strip_does_not_jump_on_split_or_merge(qapp, win):
    p = win.project
    p.import_cuts([(i * 4, i * 4 + 4, str(i), {}) for i in range(60)], {"file": "x"})
    win._refresh()
    win.goto(30 * 4 + 1)
    pump(qapp, 450)  # 가운데로 미끄러지는 애니메이션이 끝날 때까지
    sb = win.cut_strip.horizontalScrollBar()
    assert sb.value() > 0
    trace = []
    sb.valueChanged.connect(trace.append)
    before = sb.value()
    win.split_at_playhead()
    pump(qapp, 200)
    assert sb.value() == before and trace == []
    cs = p.state.cuts
    win._set_cut_selection([cs[30].id, cs[31].id])
    pump(qapp, 50)
    trace.clear()
    win.merge_selected()
    pump(qapp, 200)
    assert sb.value() == before and trace == []


def test_cut_timeline_thumbs_are_prefetched_for_all_cuts(qapp, win):
    from pathlib import Path

    from ics.ui.cutstrip import CutScrubber

    win.import_xml_file(str(Path(__file__).parent / "data" / "sample_premiere.xml"))
    pump(qapp, 100)
    want = {f for c in win.project.state.cuts for f in CutScrubber.sample_frames(c.start, c.end)}
    assert _wait(qapp, lambda: all(win.cutline_thumbs.get(f) is not None for f in want), 60)
    assert not win.preview.show_info  # '1080×1920 · Fill' 글자 없음
    assert win.cut_scrub.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_window_has_no_native_title_bar_and_own_window_buttons(qapp, win):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton

    from ics.ui import frameless

    assert win.windowFlags() & Qt.WindowType.FramelessWindowHint
    names = {b.accessibleName() for b in win._topbar_frame.findChildren(QPushButton)}
    assert {"최소화", "최대화", "닫기"} <= names
    fh = win._frameless
    root = win.centralWidget()
    if not win.isMaximized():
        assert root.layout().contentsMargins().left() == frameless.EDGE  # 가장자리 끌어 크기 조절
        assert fh.edges_at(QPoint(1, 1)) == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge)
        assert fh.edges_at(QPoint(root.width() // 2, root.height() // 2)) == Qt.Edge(0)
    frameless.toggle_max(win)
    pump(qapp, 100)
    if win.isMaximized():  # 최대화하면 가장자리 여백 없음, 버튼 모양 바뀜
        assert root.layout().contentsMargins().left() == 0 and win._win_max_btn.toolTip() == "이전 크기로"
        frameless.toggle_max(win)
        pump(qapp, 100)


def test_tab_switch_changes_state_at_once_and_animates_over_it(qapp, win):
    """탭 전환: 상태(보이는 패널)는 즉시 바뀌고, 위에 덮인 전환 그림은 잠시 뒤 사라진다. 탭 표시는 미끄러져 옮겨 간다."""
    from ics.ui import motion

    win.nav_btns["feed"].click()
    assert win.tab == "feed" and win.inspector.currentIndex() == 2  # 기다리지 않아도 이미 FEED
    covers = [c for c in win.inspector.findChildren(motion._Cover) if c.isVisible()]
    assert covers and covers[0].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # 클릭은 통과
    tabs = win.nav_tabs
    pump(qapp, 600)
    assert not [c for c in win.inspector.findChildren(motion._Cover) if c.isVisible()]
    assert tabs.current() == "feed" and tabs._ind == tabs._target("feed")
    # 빠르게 연달아 바꿔도 마지막 상태가 맞다
    for k in ("story", "credits", "reels"):
        win.nav_btns[k].click()
    assert win.tab == "reels" and win.pages.currentIndex() == 0 and win.inspector.currentIndex() == 1
    pump(qapp, 600)
    assert not win.pages.findChildren(motion._Cover) or not any(
        c.isVisible() for c in win.pages.findChildren(motion._Cover)
    )


def test_reduced_motion_switches_without_overlays(qapp, win):
    from ics.ui import motion

    motion.set_enabled(False)
    try:
        win.nav_btns["feed"].click()
        assert not [c for c in win.inspector.findChildren(motion._Cover) if c.isVisible()]
        oe = win.story_panel.overlay_editor
        oe.sec_title.head.click()
        assert not oe.sec_title.body.isVisibleTo(oe)  # 애니메이션이 꺼져 있으면 바로 접힌다
        oe.sec_title.head.click()
    finally:
        motion.set_enabled(True)


def test_toast_slides_in_and_fades_out(qapp, win):
    win.toast("테스트 알림", 200)
    assert win.toast_lbl.isVisible() and win.toast_lbl.text() == "테스트 알림"
    pump(qapp, 700)
    assert not win.toast_lbl.isVisible()


def test_every_icon_used_in_the_ui_exists():
    """코드에서 쓰는 아이콘 이름이 모두 assets/icons 에 있어야 한다 (빠지면 빈 단추가 된다)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    names = set()
    for f in (root / "ics" / "ui").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        names |= set(re.findall(r'(?:icons\.icon|icons\.pixmap|icon_button|menu_button)\(\s*"([a-z0-9-]+)"', src))
        names |= set(re.findall(r'self\._(?:btn|toggle)\(\s*"([a-z0-9-]+)"', src))
        names |= set(re.findall(r'\("(?:story|reels|feed|credits)", "[A-Z]+", "([a-z0-9-]+)"', src))
    assert len(names) > 20
    missing = sorted(n for n in names if not (root / "assets" / "icons" / f"{n}.svg").is_file())
    assert missing == []


def test_dialogs_use_the_app_title_bar(qapp, win):
    from ics.ui.dialogs import ShortcutsDialog

    d = ShortcutsDialog(win.settings, win)
    d.show()
    pump(qapp, 100)
    assert d.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert d._chrome.title.text() == d.windowTitle()
    assert d.layout().contentsMargins().top() >= 42  # 제목 줄 높이만큼 내용이 내려간다
    d._chrome.close_btn.click()
    pump(qapp, 50)
    assert not d.isVisible()


def test_dialog_title_bar_ignores_events_while_being_torn_down(qapp):
    """대화상자가 정리되는 도중(파이썬 속성이 먼저 사라짐)에 이벤트가 와도 오류 창이 뜨지 않는다.
    (자동 맞춤 중 가비지 수집 시점에 "'_DialogChrome' object has no attribute 'dlg'" 오류가 떴다)"""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QVBoxLayout

    from ics.ui.frameless import ChromeDialog

    d = ChromeDialog()
    QVBoxLayout(d)
    d.show()
    pump(qapp, 50)
    ch = d._chrome
    ch.__dict__.clear()  # 정리 도중 상태 흉내
    for t in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.WindowTitleChange, QEvent.Type.Leave):
        assert ch.eventFilter(d, QEvent(t)) is False
    d.setWindowTitle("x")  # 실제 이벤트 전달도 오류 없이
    d.resize(300, 200)
    pump(qapp, 50)
    d.close()


def test_menu_items_run_after_the_menu_closes(qapp, win, monkeypatch):
    """도구(···) 메뉴 항목은 메뉴가 닫힌 뒤 실행된다. 메뉴 안에서 바로 창을 열면 메뉴가 화면에 남아 멈춘 것처럼 보였다."""
    from PySide6.QtWidgets import QToolButton

    from ics.ui import update_ui

    calls = []
    monkeypatch.setattr(update_ui, "check_and_prompt", lambda *a, **k: calls.append("update"))
    tb = [b for b in win._topbar_frame.findChildren(QToolButton) if b.accessibleName() == "도구"][0]
    act = [a for a in tb.menu().actions() if a.text() == "업데이트…"][0]
    act.trigger()
    assert calls == []  # 메뉴 안에서 바로 실행하지 않는다
    pump(qapp, 50)
    assert calls == ["update"]


def test_about_dialog_shows_version_and_developer(qapp, win, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QToolButton

    from ics import __version__
    from ics.ui import about

    tb = [b for b in win._topbar_frame.findChildren(QToolButton) if b.accessibleName() == "도구"][0]
    assert "프로그램 정보" in [a.text() for a in tb.menu().actions()]
    d = about.AboutDialog(win)
    d.show()
    pump(qapp, 50)
    assert f"v{__version__}" in d.version_lbl.text()
    d.copy_info()
    text = QGuiApplication.clipboard().text()
    assert about.DEVELOPER in text and f"v{__version__}" in text and "FFmpeg" in text
    d.close()


def test_click_every_button_menu_and_shortcut_without_errors(qapp, win, monkeypatch):
    """모든 탭에서 보이는 단추, 메뉴 항목, 단축키 동작을 눌러 보고 예외가 하나도 없어야 한다 (막는 창은 바로 닫는다)."""
    import sys

    from PySide6.QtWidgets import QAbstractButton, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox, QToolButton

    errors: list[str] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append(f"{t.__name__}: {v}"))

    def fake_exec(self):
        self.show()
        pump(qapp, 30)
        self.reject()
        return 0

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    for n in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: None)
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda *a: True))
    skip = ("닫기", "최소화", "최대화", "이전 크기로", "프로젝트 목록으로 돌아가기")
    for tab in ("story", "reels", "feed", "credits"):
        win.set_tab(tab)
        pump(qapp, 100)
        for b in list(win.findChildren(QAbstractButton)):
            if not b.isVisible() or not b.isEnabled() or any(s in (b.accessibleName() or b.toolTip()) for s in skip):
                continue
            if isinstance(b, QToolButton) and b.menu():
                for a in b.menu().actions():
                    if not a.isSeparator() and a.isEnabled():
                        a.trigger()
                        pump(qapp, 20)
                continue
            b.click()
            pump(qapp, 10)
    for a in list(win.actions.values()):
        a.trigger()
        pump(qapp, 10)
    assert errors == []


def test_topbar_buttons_keep_their_size_in_a_narrow_window(qapp, win):
    """좁은 창에서도 '게시' / '내보내기' 글자가 잘리지 않는다 (제목 쪽이 먼저 줄어든다)."""
    from PySide6.QtWidgets import QPushButton

    win.resize(1100, 700)
    pump(qapp, 150)
    for text in ("게시", "내보내기"):
        b = [x for x in win._topbar_frame.findChildren(QPushButton) if x.text() == text][0]
        assert b.width() >= b.sizeHint().width(), (text, b.width(), b.sizeHint().width())
    assert win.nav_btns["feed"].count in ("", str(len(win.project.feed_included())))


def test_shortcuts_still_reach_the_editor_after_clicking_icon_buttons(qapp, win):
    """아이콘 단추(반복/안전 영역/보기/소리/탭)를 누른 뒤에도 Space 는 재생, → 는 1프레임 이동이어야 한다
    (단추가 키를 가져가 토글되면 안 됨)."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    tr = win.transport
    for b in (tr.loop, tr.safe, tr.view_btn, tr.mute_btn, win.nav_btns["reels"]):
        win.playback.pause()
        win.goto(30)
        pump(qapp, 30)
        QTest.mouseClick(b, Qt.MouseButton.LeftButton)
        if b.isCheckable() and b is not win.nav_btns["reels"]:
            QTest.mouseClick(b, Qt.MouseButton.LeftButton)
        pump(qapp, 30)
        state = b.isChecked()
        f0 = win.playhead
        QTest.keyClick(qapp.focusWidget() or win, Qt.Key.Key_Right)
        pump(qapp, 30)
        assert win.playhead == f0 + 1
        QTest.keyClick(qapp.focusWidget() or win, Qt.Key.Key_Space)
        pump(qapp, 30)
        assert win.playback.playing and b.isChecked() == state
        win.playback.pause()
