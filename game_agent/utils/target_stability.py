from __future__ import annotations

import importlib.util
import logging
import socket
import sys
from functools import lru_cache
from typing import Any

from game_agent.paths import REPO_ROOT

logger = logging.getLogger(__name__)

CHECKER_SCRIPT_PATH = REPO_ROOT / "GameTurbo-Native" / "check_target_stability.py"


@lru_cache(maxsize=1)
def get_checker_module() -> Any:
    path = CHECKER_SCRIPT_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"check_target_stability.py not found: {path}; "
            "ensure GameTurbo-Native submodule is ready.",
        )
    module_name = "gameturbo_check_target_stability"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    configure = getattr(mod, "configure_stdio_utf8", None)
    if callable(configure):
        configure()
    return mod


def _geo_summary_is_china(summary: str) -> bool:
    return "China" in summary or "中国" in summary


def _geo_lookup_failed(summary: str) -> bool:
    return not summary or "UNKNOWN" == summary or "归属地查询失败" in summary


def probe_domain_stability(
    domain: str,
    *,
    port: int = 443,
    ping_count: int = 1,
    timeout_fast: float = 3.0,
    timeout_slow: float = 8.0,
    tcp_attempts: int = 1,
    first_ip_only: bool = True,
) -> dict[str, Any]:
    """
    调用 GameTurbo-Native/check_target_stability.py 中的函数，
    行为对齐 extract_domain_region_from_log.sh / run_checker：
      --count 1 --timeout 3|8 --tcp-attempts 1 --first-ip-only --port <port>
    返回结构化结果（含归属地、Ping、TCP、稳定性结论）。
    """
    checker = get_checker_module()

    try:
        ips: list[str] = checker.resolve_target(domain)
    except socket.gaierror as exc:
        return {
            "domain": domain,
            "resolved_ips": [],
            "geo_summary": "UNKNOWN",
            "is_china": False,
            "ip_probes": [],
            "error": f"Target resolve failed: {exc}",
            "checker_script": str(CHECKER_SCRIPT_PATH),
        }

    if not ips:
        return {
            "domain": domain,
            "resolved_ips": [],
            "geo_summary": "UNKNOWN",
            "is_china": False,
            "ip_probes": [],
            "error": "No usable IP resolved",
            "checker_script": str(CHECKER_SCRIPT_PATH),
        }

    # 与 bash 一致：首个 IP 归属地；失败则用更长 timeout 再试一次
    timeout = timeout_fast
    first_geo = checker.lookup_geo(ips[0], timeout)
    first_summary = first_geo.get("summary") or ""
    if _geo_lookup_failed(first_summary):
        timeout = timeout_slow
        first_geo = checker.lookup_geo(ips[0], timeout)
        first_summary = first_geo.get("summary") or ""

    ips_to_probe = ips[:1] if first_ip_only else ips
    ip_probes: list[dict[str, Any]] = []
    for index, ip in enumerate(ips_to_probe, start=1):
        geo = first_geo if index == 1 else checker.lookup_geo(ip, timeout)
        summary = geo.get("summary") or "UNKNOWN"

        ping_result = checker.ping_target(ip, ping_count, timeout)
        tcp_result: dict[str, Any] | None = None
        if port:
            tcp_result = checker.tcp_probe(ip, port, tcp_attempts, timeout)

        stability = checker.stability_level(
            ping_result.get("packet_loss"),
            ping_result.get("avg_latency"),
            tcp_result["success_rate"] if tcp_result else None,
        )

        ip_probes.append(
            {
                "index": index,
                "ip": ip,
                "geo_summary": summary,
                "geo_source": geo.get("source"),
                "ping": ping_result,
                "tcp": tcp_result,
                "stability_level": stability,
            },
        )

    primary_summary = first_summary or (
        ip_probes[0]["geo_summary"] if ip_probes else "UNKNOWN"
    )

    return {
        "domain": domain,
        "resolved_ips": ips,
        "geo_summary": primary_summary,
        "geo_source": ip_probes[0].get("geo_source") if ip_probes else None,
        "is_china": _geo_summary_is_china(primary_summary),
        "port": port,
        "probe_timeout_s": timeout,
        "ping_count": ping_count,
        "tcp_attempts": tcp_attempts,
        "ip_probes": ip_probes,
        "checker_script": str(CHECKER_SCRIPT_PATH.resolve()),
    }
