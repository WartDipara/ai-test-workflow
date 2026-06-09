from __future__ import annotations

from dataclasses import dataclass

from game_agent.services.gameturbo_log_anomaly import is_fatal_gameturbo_log_line


@dataclass(frozen=True, slots=True)
class LogHealthVerdict:
    suspect: bool
    reason: str
    markers: tuple[str, ...] = ()


def _meaningful_lines(log_text: str) -> list[str]:
    out: list[str] = []
    for raw in log_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("---------"):
            continue
        out.append(line)
    return out


def assess_gameturbo_log_health(
    log_text: str,
    *,
    min_lines: int = 15,
) -> LogHealthVerdict:
    """扫描已归档的 GameTurbo 日志，输出日志通道 suspect 判定。"""
    lines = _meaningful_lines(log_text)
    if not lines:
        return LogHealthVerdict(False, "", ())

    blob = "\n".join(lines)
    lower = blob.lower()

    for line in lines:
        if not is_fatal_gameturbo_log_line(line):
            continue
        if "idle shutdown" in lower and "[sni-" not in lower:
            return LogHealthVerdict(
                True,
                "idle shutdown with zero SNI routing",
                ("fatal", "no_sni"),
            )
        return LogHealthVerdict(
            True,
            f"fatal GameTurbo marker: {line[:240]}",
            ("fatal",),
        )

    if len(lines) < min_lines:
        return LogHealthVerdict(False, "", ())

    pending = blob.count("[PENDING-SNI]")
    sni_tunnel = blob.count("[SNI-TUNNEL]")
    sni_direct = blob.count("[SNI-DIRECT]")
    send_tunnel = blob.count("[SEND-TUNNEL]")
    e2e = blob.count("E2E RTT")
    bhook = blob.count("[BHOOK] OK")

    if pending >= 3 and sni_tunnel == 0 and sni_direct == 0:
        return LogHealthVerdict(
            True,
            f"{pending} PENDING-SNI without SNI-DIRECT/TUNNEL routing",
            ("pending_no_route",),
        )

    if sni_tunnel > 0 and send_tunnel == 0 and len(lines) >= 25:
        return LogHealthVerdict(
            True,
            "SNI-TUNNEL present but no SEND-TUNNEL data plane",
            ("no_send_tunnel",),
        )

    if e2e == 0 and bhook == 0:
        return LogHealthVerdict(
            True,
            "no E2E RTT and no [BHOOK] OK — acceleration may not be active",
            ("no_accel_signals",),
        )

    if pending >= 5 and (sni_tunnel + sni_direct) < 2:
        return LogHealthVerdict(
            True,
            "SNI routing scarce relative to pending connections",
            ("routing_stall",),
        )

    return LogHealthVerdict(False, "", ())
