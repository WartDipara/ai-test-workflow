from __future__ import annotations

import locale
import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from game_agent.paths import REPO_ROOT
from game_agent.services.adb_service import AdbService
from game_agent.services.deploy_build_lock import deploy_build_locked
from game_agent.services.install_monitor.base import BaseInstallMonitor
from game_agent.services.install_monitor.factory import create_install_monitor
from game_agent.services.install_monitor.result import InstallMonitorResult
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.shutdown import ShutdownRequested, is_shutdown_requested
from game_agent.services.subprocess_tree import popen_communicate_poll
from game_agent.utils.gameturbo_bootstrap import (
    finalize_merged_config_after_deploy,
    resolve_merged_config_deploy_path,
)

logger = logging.getLogger(__name__)

ANDROID_DIR = REPO_ROOT / "GameTurbo-Native" / "client" / "android"
DEPLOY_SCRIPT = ANDROID_DIR / "deploy.sh"


@dataclass(frozen=True, slots=True)
class DeployResult:
    command: list[str]
    cwd: Path
    log_path: Path | None
    returncode: int
    install_monitor_summary: str = ""
    merged_config_path: Path | None = None


def _decode_output(data: bytes) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode(locale.getpreferredencoding(), errors="replace")
        except (LookupError, UnicodeDecodeError):
            return data.decode("utf-8", errors="replace")


def _find_bash() -> str:
    found = shutil.which("bash")
    if found:
        return found
    candidates = [
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/bash.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "bash"


def verify_package_on_device(
    package_name: str,
    *,
    serial: str | None = None,
) -> None:
    """deploy.sh 返回 0 后仍须确认 pm path（install 失败时脚本可能未报错）。"""
    pkg = (package_name or "").strip()
    if not pkg:
        raise RuntimeError("verify_package_on_device: empty package_name")
    adb = AdbService(serial)
    if adb.is_package_installed(pkg):
        logger.info("deploy 后校验: 设备已安装 %s", pkg)
        return
    detail = adb.shell(f"pm path {pkg}", timeout=15.0).strip() or "(empty)"
    raise RuntimeError(
        f"deploy.sh exited 0 but package {pkg} is not installed on device. "
        f"pm path output: {detail[:300]}. "
        "Check deploy.log for adb install (Success/Failure)."
    )


def _run_monitor_thread(
    monitor: BaseInstallMonitor,
    adb: AdbService,
    stop_event: threading.Event,
    shot_dir: Path | None,
    result_holder: list[InstallMonitorResult],
) -> None:
    try:
        monitor.monitor_install(adb, stop_event, shot_dir)
    except Exception as e:
        monitor.result.thread_crashed = True
        monitor.record_error(f"thread_crash: {e}")
        logger.exception("安装监控线程异常退出")
    finally:
        result_holder.append(monitor.result)


def run_deploy(
    gid: str,
    *,
    serial: str | None = None,
    artifact_root: Path | None = None,
    log_filename: str = "deploy.log",
    timeout_s: float = 900.0,
    expected_package: str | None = None,
    install_monitor: BaseInstallMonitor | None = None,
    output_apk: str | None = None,
    merged_config_output: Path | None = None,
) -> DeployResult:
    """Run GameTurbo android deploy in Git Bash and wait for it to finish."""
    if not DEPLOY_SCRIPT.is_file():
        raise RuntimeError(f"找不到 deploy.sh: {DEPLOY_SCRIPT}")

    cmd = [_find_bash(), "-l", "./deploy.sh", "-g", gid, "-n"]
    if serial:
        cmd.extend(["-d", serial])

    resolved_output = (output_apk or "").strip()
    if resolved_output:
        # deploy.sh 将 packages/ 相对路径解析到 client/android/packages/
        cmd.extend(["-o", f"packages/{Path(resolved_output).name}"])

    merged_deploy_path = resolve_merged_config_deploy_path(
        gid,
        artifact_root=artifact_root,
        merged_config_output=merged_config_output,
    )
    if merged_deploy_path is not None:
        # 传绝对路径，deploy.sh 直接写入 artifact，不污染 GameTurbo-Native
        cmd.extend(["-m", merged_deploy_path.as_posix()])

    log_path = (artifact_root / log_filename) if artifact_root else None
    logger.info("执行 GameTurbo deploy: %s", " ".join(cmd))

    if install_monitor is None:
        install_monitor = create_install_monitor(AdbService(serial))

    stop_event = threading.Event()
    shot_dir = (artifact_root / "install_monitor") if artifact_root else None
    if shot_dir is not None:
        shot_dir.mkdir(parents=True, exist_ok=True)
    monitor_results: list[InstallMonitorResult] = []
    monitor_thread = threading.Thread(
        target=_run_monitor_thread,
        args=(install_monitor, AdbService(serial), stop_event, shot_dir, monitor_results),
        daemon=True,
        name="install_monitor",
    )

    with trace_operation(
        "deploy",
        "run_deploy.sh",
        gid=gid,
        command=cmd,
        cwd=str(ANDROID_DIR),
    ) as rec:
        monitor_thread.start()
        popen_result = None
        try:
            with deploy_build_locked():
                popen_result = popen_communicate_poll(
                    cmd,
                    cwd=str(ANDROID_DIR),
                    timeout_s=timeout_s,
                    should_stop=is_shutdown_requested,
                    stream_output=True,
                    stream_prefix="[deploy]",
                )
        finally:
            stop_event.set()
            monitor_thread.join(timeout=15)
            if monitor_thread.is_alive():
                logger.warning("安装监控线程在 15s 内未结束")

        monitor_summary = ""
        if monitor_results:
            monitor_summary = monitor_results[0].summary()
            logger.info("安装监控汇总: %s", monitor_summary)
        elif install_monitor.result.polls or install_monitor.result.errors:
            monitor_summary = install_monitor.result.summary()

        assert popen_result is not None
        if popen_result.shutdown:
            if log_path is not None:
                log_body_shutdown = [
                    "$ " + " ".join(cmd),
                    "",
                    "=== shutdown ===",
                    "deploy interrupted by user (SIGINT cascade)",
                    "",
                    "=== stdout ===",
                    _decode_output(popen_result.stdout),
                    "=== stderr ===",
                    _decode_output(popen_result.stderr),
                ]
                if monitor_summary:
                    log_body_shutdown.extend(
                        ["", "=== install_monitor ===", monitor_summary],
                    )
                log_path.write_text("\n".join(log_body_shutdown), encoding="utf-8")
            rec.fail(error="shutdown", log_path=str(log_path) if log_path else None)
            raise ShutdownRequested("deploy interrupted by user")

        if popen_result.timed_out:
            if log_path is not None:
                log_path.write_text(
                    "\n".join(
                        [
                            "$ " + " ".join(cmd),
                            "",
                            f"=== timeout after {timeout_s}s ===",
                            "=== stdout ===",
                            _decode_output(popen_result.stdout),
                            "=== stderr ===",
                            _decode_output(popen_result.stderr),
                        ],
                    ),
                    encoding="utf-8",
                )
            rec.fail(error=f"timeout={timeout_s}", log_path=str(log_path) if log_path else None)
            raise RuntimeError(
                f"deploy.sh 超时 ({timeout_s:.0f}s)，日志: {log_path or '未落盘'}",
            )

        result_returncode = popen_result.returncode
        log_body = [
            "$ " + " ".join(cmd),
            "",
            "=== stdout ===",
            _decode_output(popen_result.stdout),
            "=== stderr ===",
            _decode_output(popen_result.stderr),
        ]
        if monitor_summary:
            log_body.extend(["", "=== install_monitor ===", monitor_summary])
        if install_monitor.result.errors:
            log_body.extend(
                ["", "=== install_monitor_errors ===", *install_monitor.result.errors],
            )

        if log_path is not None:
            log_path.write_text("\n".join(log_body), encoding="utf-8")

        if result_returncode != 0:
            rec.fail(
                f"exit={result_returncode}",
                returncode=result_returncode,
                log_path=str(log_path) if log_path else None,
            )
            raise RuntimeError(
                f"deploy.sh 失败 (exit={result_returncode})，日志: {log_path or '未落盘'}",
            )

        if expected_package:
            try:
                verify_package_on_device(expected_package, serial=serial)
            except RuntimeError as e:
                if monitor_summary:
                    raise RuntimeError(f"{e} | install_monitor: {monitor_summary}") from e
                raise

        rec.ok(returncode=0, log_path=str(log_path) if log_path else None)

    merged_final: Path | None = None
    if merged_deploy_path is not None:
        merged_final = finalize_merged_config_after_deploy(gid, merged_deploy_path)

    logger.info("GameTurbo deploy 完成 (gid=%s)", gid)
    return DeployResult(
        command=cmd,
        cwd=ANDROID_DIR,
        log_path=log_path,
        returncode=result_returncode,
        install_monitor_summary=monitor_summary,
        merged_config_path=merged_final,
    )
