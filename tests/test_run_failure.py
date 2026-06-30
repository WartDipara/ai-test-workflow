from game_agent.models.run_failure import ErrorCode, classify_failure, compact_failure_message


def test_e1009_package_install_not_retryable() -> None:
    failure = classify_failure(
        "[E1009] Package com.foo not on device before executor.",
    )
    assert failure.code == ErrorCode.PACKAGE_INSTALL
    assert failure.retryable is False


def test_e2006_explicit_code_is_retryable() -> None:
    failure = classify_failure("[E2006] Executor network routing issue")
    assert failure.code == ErrorCode.EXECUTOR_NETWORK
    assert failure.retryable is True


def test_log_anomaly_no_longer_retryable_at_runtime() -> None:
    failure = classify_failure("Log anomaly detected: tunnel closed")
    assert failure.retryable is False


def test_vision_ocr_anomaly_retryable() -> None:
    failure = classify_failure(
        "Vision/OCR network anomaly confirmed: ui_stage=download ocr=network dialog"
    )
    assert failure.code == ErrorCode.NET_SCREEN_ANOMALY
    assert failure.retryable is True


def test_package_timeout_classified_e1009() -> None:
    failure = classify_failure("timeout: package com.foo not installed on device")
    assert failure.code == ErrorCode.PACKAGE_INSTALL
    assert failure.retryable is False


def test_asyncio_nested_run_classified_e1004_not_e1006() -> None:
    reason = (
        "GameTurbo 前置处理失败: asyncio.run() cannot be called from a running event loop"
    )
    failure = classify_failure(reason)
    assert failure.code == ErrorCode.DEPLOY_INFRA
    assert failure.retryable is False


def test_gameturbo_prepare_winerror_classified_e1004() -> None:
    reason = "GameTurbo 前置处理失败: [WinError 2] 系统找不到指定的文件"
    failure = classify_failure(reason)
    assert failure.code == ErrorCode.DEPLOY_INFRA
    assert failure.retryable is False


def test_foreground_recover_failure_retryable_e2007() -> None:
    reason = (
        "前台应用丢失: foreground recover failed after 5 attempts "
        "(foreground=com.android.chrome, target=com.game.app, detail=verify failed)"
    )
    failure = classify_failure(reason)
    assert failure.code == ErrorCode.FOREGROUND_LOST
    assert failure.retryable is True


def test_compact_failure_message_preserves_e2006_tail() -> None:
    probe = "[ServerProbe] " + ("x" * 2500)
    tail = "[ServerCheck] FAILED after 3 tap(s) — Use report_flow_done with [E2006]."
    long_msg = probe + "\n" + tail
    assert len(long_msg) > 2000
    compact = compact_failure_message(long_msg, max_len=2000)
    assert "[E2006]" in compact
    assert "[ServerCheck]" in compact
    assert compact.startswith("[E2006]")
