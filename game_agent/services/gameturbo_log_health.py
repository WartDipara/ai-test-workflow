from __future__ import annotations

from dataclasses import dataclass

from game_agent.services.gameturbo_log_anomaly import is_fatal_gameturbo_log_line

_PRE_GAME_INIT_STAGES = frozenset({
    "privacy",
    "privacy_agree",
    "launch",
    "splash",
    "login",
    "login_form",
})


def _is_pre_game_init_stage(stage: str) -> bool:
    return (stage or "").strip().lower() in _PRE_GAME_INIT_STAGES


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
    ui_stage: str = "",
) -> LogHealthVerdict:
    """扫描已归档的 GameTurbo 日志，输出日志通道 suspect 判定。"""
    stage = (ui_stage or "").strip().lower()
    lines = _meaningful_lines(log_text)
    if not lines:
        return LogHealthVerdict(False, "", ())

    blob = "\n".join(lines)
    pre_init = _is_pre_game_init_stage(stage)

    for line in lines:
        if not is_fatal_gameturbo_log_line(line):
            continue
        line_lower = line.lower()
        if pre_init and "idle shutdown" in line_lower and "[sni-" not in line_lower:
            continue
        if "idle shutdown" in line_lower and "[sni-" not in line_lower:
            return LogHealthVerdict(
                True,
                "idle shutdown with zero SNI routing",
                ("fatal", "no_sni"),
            )
        if pre_init:
            continue
        return LogHealthVerdict(
            True,
            f"fatal GameTurbo marker: {line[:240]}",
            ("fatal",),
        )

    if len(lines) < min_lines:
        return LogHealthVerdict(False, "", ())

    if pre_init:
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

    if (
        sni_tunnel > 0
        and send_tunnel == 0
        and len(lines) >= 25
        and stage in ("resource_download", "loading")
    ):
        return LogHealthVerdict(
            True,
            "SNI-TUNNEL present but no SEND-TUNNEL data plane (during resource_download)",
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
