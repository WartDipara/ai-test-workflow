from game_agent.models.run_failure import ErrorCode, classify_failure


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
