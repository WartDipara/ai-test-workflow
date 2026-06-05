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
from game_agent.services.install_monitor.base import BaseInstallMonitor
from game_agent.services.install_monitor.factory import create_install_monitor
from game_agent.services.pipeline_trace import trace_operation

logger = logging.getLogger(__name__)

ANDROID_DIR = REPO_ROOT / "GameTurbo-Native" / "client" / "android"
DEPLOY_SCRIPT = ANDROID_DIR / "deploy.sh"


@dataclass(frozen=True, slots=True)
class DeployResult:
    command: list[str]
    cwd: Path
    log_path: Path | None
    returncode: int


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


def run_deploy(
    gid: str,
    *,
    serial: str | None = None,
    artifact_root: Path | None = None,
    log_filename: str = "deploy.log",
    timeout_s: float = 900.0,
    expected_package: str | None = None,
    install_monitor: BaseInstallMonitor | None = None,
) -> DeployResult:
    """Run GameTurbo android deploy in Git Bash and wait for it to finish."""
    if not DEPLOY_SCRIPT.is_file():
        raise RuntimeError(f"找不到 deploy.sh: {DEPLOY_SCRIPT}")

    cmd = [_find_bash(), "-l", "./deploy.sh", "-g", gid, "-n"]
    if serial:
        cmd.extend(["-d", serial])

    log_path = (artifact_root / log_filename) if artifact_root else None
    logger.info("执行 GameTurbo deploy: %s", " ".join(cmd))

    if install_monitor is None:
        install_monitor = create_install_monitor(AdbService(serial))

    stop_event = threading.Event()
    monitor_thread = threading.Thread(
        target=install_monitor.monitor_install,
        args=(AdbService(serial), stop_event),
        daemon=True,
    )

    with trace_operation(
        "deploy",
        "run_deploy.sh",
        gid=gid,
        command=cmd,
        cwd=str(ANDROID_DIR),
    ) as rec:
        monitor_thread.start()
        try:
            result = subprocess.run(
                cmd,
                cwd=ANDROID_DIR,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        finally:
            stop_event.set()
            monitor_thread.join(timeout=10)

        if log_path is not None:
            log_path.write_text(
                "\n".join(
                    [
                        "$ " + " ".join(cmd),
                        "",
                        "=== stdout ===",
                        _decode_output(result.stdout),
                        "=== stderr ===",
                        _decode_output(result.stderr),
                    ],
                ),
                encoding="utf-8",
            )

        if result.returncode != 0:
            rec.fail(
                f"exit={result.returncode}",
                returncode=result.returncode,
                log_path=str(log_path) if log_path else None,
            )
            raise RuntimeError(
                f"deploy.sh 失败 (exit={result.returncode})，日志: {log_path or '未落盘'}",
            )

        if expected_package:
            verify_package_on_device(expected_package, serial=serial)

        rec.ok(returncode=0, log_path=str(log_path) if log_path else None)
    logger.info("GameTurbo deploy 完成 (gid=%s)", gid)
    return DeployResult(
        command=cmd,
        cwd=ANDROID_DIR,
        log_path=log_path,
        returncode=result.returncode,
    )

