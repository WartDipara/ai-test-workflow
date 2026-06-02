from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from game_agent.models.gameturbo_config import GameTurboConfigPatch
from game_agent.services.pipeline_trace import get_pipeline_tracer


@dataclass(frozen=True, slots=True)
class ConfigApplyResult:
    path: Path
    changed: bool
    summary: list[str]


def _merge_patterns(existing: list[Any], additions: list[str]) -> tuple[list[Any], int]:
    seen = {item for item in existing if isinstance(item, str)}
    merged = list(existing)
    added = 0
    for pattern in additions:
        clean = pattern.strip()
        if not clean or clean in seen:
            continue
        merged.append(clean)
        seen.add(clean)
        added += 1
    return merged, added


def _merge_port_rules(
    existing: list[Any],
    updates: list[dict[str, Any]],
) -> tuple[list[Any], int]:
    by_port: dict[int, dict[str, Any]] = {}
    passthrough: list[Any] = []
    for item in existing:
        if isinstance(item, dict) and isinstance(item.get("port"), int):
            by_port[item["port"]] = item
        else:
            passthrough.append(item)

    changed = 0
    for rule in updates:
        port = rule.get("port")
        if not isinstance(port, int):
            continue
        old = by_port.get(port)
        if old != rule:
            changed += 1
        by_port[port] = rule

    merged = passthrough + [by_port[port] for port in sorted(by_port)]
    return merged, changed


def apply_gameturbo_config_patch(
    config_path: Path,
    patch: GameTurboConfigPatch,
) -> ConfigApplyResult:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"GameTurbo 配置不是 JSON object: {config_path}")

    summary: list[str] = []

    if patch.port_rules:
        merged, changed_count = _merge_port_rules(
            data.get("port_rules") if isinstance(data.get("port_rules"), list) else [],
            patch.port_rules,
        )
        if changed_count:
            data["port_rules"] = merged
            summary.append(f"port_rules: merged {changed_count} rule(s)")

    if patch.direct_patterns:
        merged, added_count = _merge_patterns(
            data.get("direct_patterns") if isinstance(data.get("direct_patterns"), list) else [],
            patch.direct_patterns,
        )
        if added_count:
            data["direct_patterns"] = merged
            summary.append(f"direct_patterns: added {added_count} pattern(s)")

    changed = bool(summary)
    if changed:
        config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )

    tracer = get_pipeline_tracer()
    if tracer:
        tracer.record(
            "config_apply",
            "apply_gameturbo_config_patch",
            status="ok" if changed else "skip",
            detail={"path": str(config_path), "summary": summary},
        )

    return ConfigApplyResult(path=config_path, changed=changed, summary=summary)

