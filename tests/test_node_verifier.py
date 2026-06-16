"""NodeVerifier OCR 差分验证。"""

from __future__ import annotations

from game_agent.services.node_verifier import verify_stage_exit


def test_sub_account_exit_panel_gone() -> None:
    before = "Sub-account1 (Last login)\nCreate Sub-account"
    after = "Click to select Server\n踏入仙途"
    result = verify_stage_exit(
        ocr_before=before,
        ocr_after=after,
        expected_stage="sub_account_select",
    )
    assert result.passed is True
    assert "sub-account" in result.reason.lower() or "server" in result.reason.lower()


def test_sub_account_still_blocking() -> None:
    text = "小号1 上次登录\n创建小号"
    result = verify_stage_exit(
        ocr_before=text,
        ocr_after=text,
        expected_stage="sub_account_select",
    )
    assert result.passed is False


def test_completion_signals() -> None:
    result = verify_stage_exit(
        ocr_before="foo",
        ocr_after="选服 踏入仙途",
        expected_stage="sub_account_select",
        completion_signals=["选服", "踏入"],
    )
    assert result.passed is True
