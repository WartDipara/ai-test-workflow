from game_agent.services.server_selector_check import (
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


def test_same_screen_list_rows() -> None:
    enter = _enter()
    before = [enter, _bbox("Click to select Server", 1050, 620, 1400, 660)]
    after = before + [
        _bbox("华东一区", 1000, 500, 1200, 540),
        _bbox("华南二区", 1000, 560, 1200, 600),
    ]
    assert server_list_panel_opened(before, after, enter) is True


def test_no_panel_minor_change() -> None:
    enter = _enter()
    before = [enter]
    after = [enter]
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
