from __future__ import annotations

import locale
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from game_agent.external_services.gameturbo.bootstrap import (
    finalize_merged_config_after_deploy,
    resolve_merged_config_deploy_path,
)
from game_agent.external_services.gameturbo.paths import GAMETURBO_NATIVE_DIR
from game_agent.services.adb_service import AdbService
from game_agent.services.deploy_build_lock import deploy_build_locked
from game_agent.services.install_monitor.base import BaseInstallMonitor
from game_agent.services.install_with_monitor import (
    InstallMonitorSession,
    summarize_monitor_result,
)
from game_agent.services.package_install import (
    verify_package_on_device as _verify_package_on_device,
)
from game_agent.services.pipeline_trace import trace_operation
from game_agent.services.shutdown import ShutdownRequested, is_shutdown_requested
from game_agent.services.subprocess_tree import popen_communicate_poll

logger = logging.getLogger(__name__)

ANDROID_DIR = GAMETURBO_NATIVE_DIR / "client" / "android"
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


def _find_bash(bash_path: str | None = None) -> str:
    custom = (bash_path or "").strip()
    if custom:
        candidate = Path(custom)
        if candidate.is_file():
            return str(candidate.resolve())
    found = shutil.which("bash")
    if found:
        return found
    candidates = [
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/bash.exe"),
        Path("C:/Program Files (x86)/Git/bin/bash.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    if custom:
        raise RuntimeError(f"Configured bash_path not found: {custom}")
    return "bash"


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
    bash_path: str | None = None,
) -> DeployResult:
    """Run GameTurbo android deploy in Git Bash and wait for it to finish."""
    if not DEPLOY_SCRIPT.is_file():
        raise RuntimeError(f"deploy.sh not found: {DEPLOY_SCRIPT}")

    cmd = [_find_bash(bash_path), "-l", "./deploy.sh", "-g", gid, "-n"]
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
    logger.info("Running GameTurbo deploy: %s", " ".join(cmd))

    adb = AdbService(serial)
    monitor_session = InstallMonitorSession.start(
        adb,
        artifact_root=artifact_root,
        install_monitor=install_monitor,
    )
    install_monitor = monitor_session.monitor

    with trace_operation(
        "deploy",
        "run_deploy.sh",
        gid=gid,
        command=cmd,
        cwd=str(ANDROID_DIR),
    ) as rec:
        popen_result = monitor_session.run_while(
            lambda: _run_deploy_subprocess(cmd, timeout_s=timeout_s),
        )
        monitor_summary = summarize_monitor_result(
            install_monitor,
            monitor_session._results,
        )

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
                _verify_package_on_device(adb, expected_package)
            except RuntimeError as e:
                if monitor_summary:
                    raise RuntimeError(f"{e} | install_monitor: {monitor_summary}") from e
                raise

        rec.ok(returncode=0, log_path=str(log_path) if log_path else None)

    merged_final: Path | None = None
    if merged_deploy_path is not None:
        merged_final = finalize_merged_config_after_deploy(gid, merged_deploy_path)

    logger.info("GameTurbo deploy done (gid=%s)", gid)
    return DeployResult(
        command=cmd,
        cwd=ANDROID_DIR,
        log_path=log_path,
        returncode=result_returncode,
        install_monitor_summary=monitor_summary,
        merged_config_path=merged_final,
    )


def _run_deploy_subprocess(cmd: list[str], *, timeout_s: float):
    with deploy_build_locked():
        return popen_communicate_poll(
            cmd,
            cwd=str(ANDROID_DIR),
            timeout_s=timeout_s,
            should_stop=is_shutdown_requested,
            stream_output=True,
            stream_prefix="[deploy]",
        )
