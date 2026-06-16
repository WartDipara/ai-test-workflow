from game_agent.services.server_selector_check import (
    evaluate_panel_ocr,
    find_dismiss_tap,
    find_exit_confirm_negative,
    has_exit_confirm_dialog,
    is_page_navigation,
    server_list_panel_opened,
)
from game_agent.utils.ocr_util import OcrBbox


def _bbox(text: str, x1: int, y1: int, x2: int, y2: int) -> OcrBbox:
    return OcrBbox(
        text=text,
        cx=(x1 + x2) // 2,
        cy=(y1 + y2) // 2,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
    )


def _enter() -> OcrBbox:
    return _bbox("踏入仙途", 1100, 770, 1300, 820)


def test_server_list_panel_by_title() -> None:
    enter = _enter()
    before = [enter, _bbox("Click to select Server", 1050, 620, 1400, 660)]
    after = before + [_bbox("选择服务器", 900, 300, 1100, 340), _bbox("关闭", 1200, 200, 1280, 240)]
    assert server_list_panel_opened(before, after, enter) is True


def test_page_navigation_not_panel() -> None:
    """子账号页 → 出现踏入仙途：整页跳转，非弹窗。"""
    enter_before = _bbox("Sub-account1", 1500, 250, 1900, 290)
    before = [enter_before]
    after = [_bbox("踏入仙途", 1100, 770, 1300, 820), _bbox("I have read and agree", 900, 880, 1300, 920)]
    enter = _enter()
    assert is_page_navigation(before, after, enter) is True
    assert server_list_panel_opened(before, after, enter) is False


def test_bare_list_rows_without_modal_not_panel() -> None:
    """仅多两行区服 OCR、无弹窗标题/关闭钮 → 不算独立弹窗。"""
    enter = _enter()
    before = [enter, _bbox("Click to select Server", 1050, 620, 1400, 660)]
    after = before + [
        _bbox("华东一区", 1000, 500, 1200, 540),
        _bbox("华南二区", 1000, 560, 1200, 600),
    ]
    assert server_list_panel_opened(before, after, enter) is False


def test_select_server_modal_with_close() -> None:
    """参考弹窗：标题 + 关闭 + 区服行。"""
    enter = _enter()
    before = [enter, _bbox("Click to select Server", 1050, 620, 1400, 660)]
    after = before + [
        _bbox("Select Server", 900, 180, 1100, 220),
        _bbox("X", 1800, 160, 1840, 200),
        _bbox("Free 1Flying Dragon1 Server", 1000, 400, 1500, 440),
        _bbox("推荐", 700, 300, 780, 340),
    ]
    assert server_list_panel_opened(before, after, enter) is True


def test_modal_close_plus_category_tabs() -> None:
    enter = _enter()
    before = [enter, _bbox("Click to select Server", 1050, 620, 1400, 660)]
    after = before + [
        _bbox("关闭", 1200, 200, 1280, 240),
        _bbox("已有角色", 700, 350, 850, 390),
        _bbox("爆满", 900, 900, 960, 930),
    ]
    assert server_list_panel_opened(before, after, enter) is True


def test_no_panel_minor_change() -> None:
    enter = _enter()
    before = [enter]
    after = [enter]
    assert server_list_panel_opened(before, after, enter) is False


def test_dismiss_alone_not_panel() -> None:
    """仅 OCR 新识别到关闭/确定，无列表标题或多行区服 → 不算弹窗。"""
    enter = _enter()
    before = [enter, _bbox("Click to select Server", 1050, 620, 1400, 660)]
    after = before + [_bbox("确定", 500, 100, 600, 140)]
    assert server_list_panel_opened(before, after, enter) is False


def test_ocr_junk_single_chars_not_panel() -> None:
    """16914 类场景：tap 后 OCR 抖动出单字碎片，无真实区服列表。"""
    enter = _enter()
    before = [enter, _bbox("Click to select Server", 1050, 620, 1400, 660)]
    after = before + [
        _bbox("中", 1091, 646, 1110, 686),
        _bbox("福", 1000, 500, 1020, 540),
    ]
    assert server_list_panel_opened(before, after, enter) is False


def test_find_dismiss_tap_prefers_close() -> None:
    bboxes = [
        _bbox("确定", 500, 100, 600, 140),
        _bbox("关闭", 700, 100, 800, 140),
    ]
    tap = find_dismiss_tap(bboxes)
    assert tap == (750, 120)


def test_exit_confirm_negative() -> None:
    bboxes = [
        _bbox("是否退出游戏", 400, 400, 700, 440),
        _bbox("退出游戏", 300, 500, 450, 540),
        _bbox("取消", 550, 500, 650, 540),
    ]
    assert has_exit_confirm_dialog(bboxes) is True
    tap = find_exit_confirm_negative(bboxes)
    assert tap == (600, 520)


def test_run_state_format_launch_stage() -> None:
    from game_agent.models.run_state import RunState

    run = RunState(launch_stage="server_check", server_check_attempts=2)
    s = run.format_launch_stage_status()
    assert "server_check" in s
    assert "server_checked=False" in s


def _enter_start_game_portrait() -> OcrBbox:
    return _bbox("Start Game", 400, 1900, 680, 2000)


def test_17690_panel_by_qufu_title() -> None:
    """17690 竖屏：OCR 识别「选择区服」应通过快判。"""
    enter = _enter_start_game_portrait()
    before = [enter, _bbox("1 ServerXQ删档内测1服", 200, 1650, 880, 1700)]
    after = before + [
        _bbox("选择区服", 400, 450, 680, 510),
        _bbox("最近登录", 120, 580, 280, 640),
        _bbox("火爆", 200, 1780, 280, 1820),
        _bbox("流畅", 400, 1780, 480, 1820),
        _bbox("维护", 600, 1780, 680, 1820),
    ]
    verdict = evaluate_panel_ocr(before, after, enter)
    assert verdict.passed is True
    assert verdict.evidence == "modal_title"
    assert server_list_panel_opened(before, after, enter) is True
