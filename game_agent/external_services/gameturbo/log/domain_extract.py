"""
从 GameTurbo 日志提取域名/路由/归属地，输出结构化 JSON 供 AI 与 Modify 阶段使用。

逻辑对齐（不修改原文件）：
- GameTurbo-Native/extract_domain_region_from_log.sh：域名提取、SNI 路由分类、pending IP、非 China 列表
- GameTurbo-Native/check_target_stability.py：tunnel 域名的解析/归属地/Ping/TCP（经 target_stability.probe）

shell 脚本 stdout 为人类可读；本模块写入 domain_region_analysis.json。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from game_agent.paths import REPO_ROOT
from game_agent.utils.target_stability import CHECKER_SCRIPT_PATH, probe_domain_stability

logger = logging.getLogger(__name__)

EXTRACT_SCRIPT_PATH = REPO_ROOT / "GameTurbo-Native" / "extract_domain_region_from_log.sh"
SCHEMA_VERSION = 1

_DOMAIN_PATTERN = re.compile(
    r"(?<![\w-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?![\w-])",
)
_PENDING_SNI_IP_PATTERN = re.compile(
    r"\[PENDING-SNI\]\s+(?:\[\:\:ffff\:)?(\d{1,3}(?:\.\d{1,3}){3})(?:\])?:\d+",
)
_CDN_RESOURCE_DOMAIN_RE = re.compile(
    r"cdn|static|res|patch|asset|download|pkg|bundle",
    re.IGNORECASE,
)

_ALLOWED_TLDS = frozenset({
    "com", "net", "org", "io", "cn", "co", "me", "ai", "app", "cloud",
    "top", "xyz", "vip", "live", "site", "tech", "pro", "info", "mobi",
    "fm", "gg", "hk", "tw", "jp", "kr", "de", "uk", "us", "cc",
})
_DENY_SUFFIXES = frozenset({
    "so", "apk", "apks", "json", "xml", "jar", "dex", "arsc", "obb", "db",
})
_DENY_EXACT = frozenset({
    "com.android",
    "com.android.adbd",
    "com.android.art",
    "com.android.conscrypt",
    "com.android.os.statsd",
    "com.android.runtime",
    "com.android.tethering",
    "com.android.vndk",
    "vendor.mediatek.hardware",
    "android.hardware.graphics.mapper",
})

DEFAULT_OUTPUT_NAME = "domain_region_analysis.json"


def normalize_gameturbo_log_text(log_text: str) -> str:
    """统一换行并去掉 \\r，与 extract_domain_region_from_log.sh 的 tr -d '\\r' 对齐。"""
    if not log_text:
        return log_text
    return log_text.replace("\r\n", "\n").replace("\r", "\n")


def _looks_like_real_domain(value: str) -> bool:
    if value in _DENY_EXACT:
        return False
    labels = value.split(".")
    if len(labels) < 2:
        return False
    tld = labels[-1]
    if tld in _DENY_SUFFIXES:
        return False
    if tld not in _ALLOWED_TLDS:
        return False
    if all(re.fullmatch(r"[0-9]+", x) for x in labels[:-1]):
        return False
    if labels[0] in {"com", "android", "vendor"} and len(labels) >= 2:
        return False
    return True


def extract_domains_from_log_text(log_text: str) -> list[str]:
    domains: set[str] = set()
    for line in log_text.splitlines():
        for match in _DOMAIN_PATTERN.findall(line):
            value = match.strip().strip(".").lower()
            if not value:
                continue
            if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value):
                continue
            if not _looks_like_real_domain(value):
                continue
            domains.add(value)
    return sorted(domains)


def extract_pending_sni_ips_from_log_text(log_text: str) -> list[str]:
    seen: set[str] = set()
    ips: list[str] = []
    for line in log_text.splitlines():
        match = _PENDING_SNI_IP_PATTERN.search(line)
        if not match:
            continue
        ip = match.group(1)
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def _classify_route(domain: str, log_text: str) -> str:
    if f"[SNI-TUNNEL] {domain}" in log_text:
        return "tunnel"
    if f"[SNI-DIRECT] {domain}" in log_text:
        return "direct"
    return "unknown"


def _pending_ip_in_log(ip: str, log_text: str) -> bool:
    return (
        f"[PENDING-SNI] [::ffff:{ip}]:" in log_text
        or f"[PENDING-SNI] {ip}:" in log_text
    )


def _should_mark_non_china(geo_summary: str, *, is_china: bool) -> bool:
    if is_china:
        return False
    if not geo_summary or geo_summary == "UNKNOWN":
        return False
    if "归属地查询失败" in geo_summary:
        return False
    return True


def _matched_pending_ips(domain: str, resolved_ips: list[str], log_text: str) -> list[str]:
    matched: list[str] = []
    for ip in resolved_ips:
        if _pending_ip_in_log(ip, log_text):
            matched.append(ip)
    return matched


@dataclass
class DomainRegionAnalysis:
    """与 extract_domain_region_from_log.sh + check_target_stability.py 等价的结构化结果。"""

    schema_version: int
    source_log: str
    analyzed_at: str
    port: int
    extractor_module: str
    aligned_scripts: list[str]
    checker_script: str
    domain_count: int
    tunnel_domains: list[dict[str, Any]] = field(default_factory=list)
    direct_domains: list[dict[str, Any]] = field(default_factory=list)
    unknown_domains: list[dict[str, Any]] = field(default_factory=list)
    all_pending_ips: list[str] = field(default_factory=list)
    unmatched_pending_ips: list[str] = field(default_factory=list)
    non_china_domains: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    console_equivalent: list[str] = field(
        default_factory=list,
        metadata={"description": "与 shell 脚本 stdout 语义一致的行，便于人工对照"},
    )

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json_text(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json_dict(), ensure_ascii=False, indent=indent)


class GameTurboLogDomainExtractor:
    """
    从 GameTurbo logcat 导出文件中提取域名、路由类型与归属地，
    行为对齐 GameTurbo-Native/extract_domain_region_from_log.sh。
    """

    def __init__(self, *, port: int = 443) -> None:
        self.port = port

    def analyze_log_file(self, log_path: Path) -> DomainRegionAnalysis:
        if not log_path.is_file():
            raise FileNotFoundError(f"Log file not found: {log_path}")
        log_text = normalize_gameturbo_log_text(
            log_path.read_text(encoding="utf-8", errors="replace"),
        )
        return self.analyze_log_text(log_text, source_log=str(log_path.resolve()))

    def analyze_log_text(self, log_text: str, *, source_log: str = "") -> DomainRegionAnalysis:
        log_text = normalize_gameturbo_log_text(log_text)
        domains = extract_domains_from_log_text(log_text)
        all_pending = extract_pending_sni_ips_from_log_text(log_text)
        matched_pending: set[str] = set()
        non_china: list[str] = []
        tunnel_rows: list[dict[str, Any]] = []
        direct_rows: list[dict[str, Any]] = []
        unknown_rows: list[dict[str, Any]] = []
        errors: list[str] = []

        for domain in domains:
            route = _classify_route(domain, log_text)
            if route == "tunnel":
                try:
                    probe = probe_domain_stability(domain, port=self.port)
                except Exception as exc:
                    logger.warning("Domain %s stability probe failed: %s", domain, exc)
                    errors.append(f"{domain}: {exc}")
                    probe = {
                        "domain": domain,
                        "resolved_ips": [],
                        "geo_summary": "UNKNOWN",
                        "is_china": False,
                        "ip_probes": [],
                        "error": str(exc),
                    }
                pending_for_domain = _matched_pending_ips(
                    domain,
                    probe.get("resolved_ips") or [],
                    log_text,
                )
                matched_pending.update(pending_for_domain)
                if _should_mark_non_china(
                    str(probe.get("geo_summary") or ""),
                    is_china=bool(probe.get("is_china")),
                ):
                    non_china.append(domain)
                tunnel_rows.append(
                    {
                        "domain": domain,
                        "route": "tunnel",
                        "geo_summary": probe.get("geo_summary", "UNKNOWN"),
                        "geo_source": probe.get("geo_source"),
                        "is_china": bool(probe.get("is_china")),
                        "resolved_ips": probe.get("resolved_ips") or [],
                        "matched_pending_ips": pending_for_domain,
                        "probe_timeout_s": probe.get("probe_timeout_s"),
                        "stability_level": (
                            (probe.get("ip_probes") or [{}])[0].get("stability_level")
                            if probe.get("ip_probes")
                            else None
                        ),
                        "ping": (
                            (probe.get("ip_probes") or [{}])[0].get("ping")
                            if probe.get("ip_probes")
                            else None
                        ),
                        "tcp": (
                            (probe.get("ip_probes") or [{}])[0].get("tcp")
                            if probe.get("ip_probes")
                            else None
                        ),
                        "ip_probes": probe.get("ip_probes") or [],
                    },
                )
            elif route == "direct":
                direct_rows.append({"domain": domain, "route": "direct"})
            else:
                unknown_rows.append({"domain": domain, "route": "unknown"})

        unmatched = [ip for ip in all_pending if ip not in matched_pending]

        checker_path = str(CHECKER_SCRIPT_PATH.resolve()) if CHECKER_SCRIPT_PATH.is_file() else ""
        console_lines = _build_console_equivalent_lines(
            domain_count=len(domains),
            tunnel_rows=tunnel_rows,
            direct_rows=direct_rows,
            unknown_rows=unknown_rows,
            unmatched_pending_ips=unmatched,
            non_china_domains=sorted(set(non_china)),
        )

        return DomainRegionAnalysis(
            schema_version=SCHEMA_VERSION,
            source_log=source_log,
            analyzed_at=datetime.now(UTC).isoformat(),
            port=self.port,
            extractor_module="game_agent.external_services.gameturbo.log.domain_extract",
            aligned_scripts=[
                str(EXTRACT_SCRIPT_PATH.relative_to(REPO_ROOT))
                if EXTRACT_SCRIPT_PATH.is_file()
                else "GameTurbo-Native/extract_domain_region_from_log.sh",
                str(CHECKER_SCRIPT_PATH.relative_to(REPO_ROOT))
                if CHECKER_SCRIPT_PATH.is_file()
                else "GameTurbo-Native/check_target_stability.py",
            ],
            checker_script=checker_path,
            domain_count=len(domains),
            tunnel_domains=tunnel_rows,
            direct_domains=direct_rows,
            unknown_domains=unknown_rows,
            all_pending_ips=all_pending,
            unmatched_pending_ips=unmatched,
            non_china_domains=sorted(set(non_china)),
            errors=errors,
            console_equivalent=console_lines,
        )


def _build_console_equivalent_lines(
    *,
    domain_count: int,
    tunnel_rows: list[dict[str, Any]],
    direct_rows: list[dict[str, Any]],
    unknown_rows: list[dict[str, Any]],
    unmatched_pending_ips: list[str],
    non_china_domains: list[str],
) -> list[str]:
    """生成与 extract_domain_region_from_log.sh 终端输出同语义的行。"""
    lines = [f"提取到域名数: {domain_count}", ""]
    for row in tunnel_rows:
        domain = row.get("domain", "")
        geo = row.get("geo_summary", "UNKNOWN")
        lines.append(f"{domain}, tunnel, {geo}")
        for ip in row.get("matched_pending_ips") or []:
            lines.append(f"当前域名：{domain}, 对应pending-sni的IP为：{ip}")
        lines.append("")
    for row in direct_rows:
        lines.append(f"{row.get('domain', '')}, direct")
    if unmatched_pending_ips:
        lines.append("未命中pending-sni的IP如下：")
        lines.extend(unmatched_pending_ips)
    if non_china_domains:
        lines.append("非China域名如下：")
        lines.extend(non_china_domains)
    for row in unknown_rows:
        lines.append(f"{row.get('domain', '')}, unknown")
    return lines


def _cdn_resource_domain_hint(domain: str) -> bool:
    return bool(_CDN_RESOURCE_DOMAIN_RE.search(domain or ""))


def _extract_ipv6_rule_direct_lines(log_text: str, *, limit: int = 8) -> list[str]:
    if not log_text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for line in log_text.splitlines():
        if "[IPV6-RULE]" not in line or "direct" not in line.lower():
            continue
        key = line.strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= limit:
            break
    return out


def _read_source_log_text(domain_analysis: dict[str, Any]) -> str:
    source = str(domain_analysis.get("source_log") or "").strip()
    if not source:
        return ""
    path = Path(source)
    if not path.is_file():
        return ""
    try:
        return normalize_gameturbo_log_text(
            path.read_text(encoding="utf-8", errors="replace"),
        )
    except OSError:
        return ""


def format_domain_analysis_for_ai(domain_analysis: dict[str, Any] | None) -> str:
    """
    将 domain_region_analysis.json 格式化为 AI Modify/报告 必读块。
    Modify 阶段配置补丁必须以此为准，不可跳过。
    """
    if not domain_analysis:
        return (
            "[Domain/region JSON] missing.\n"
            "Modify stage: do not propose direct_patterns/port_rules patches.\n"
            "Generate "
            f"{DEFAULT_OUTPUT_NAME} from gameturbo.log via "
            "game_agent.external_services.gameturbo.log.domain_extract "
            "(aligned with extract_domain_region_from_log.sh)."
        )

    lines = [
        "[Domain/region JSON — primary for Modify]",
        f"schema_version: {domain_analysis.get('schema_version', '?')}",
        f"domain_count: {domain_analysis.get('domain_count', 0)}",
        f"checker_script: {domain_analysis.get('checker_script', '')}",
        "",
        "## tunnel_domains ([SNI-TUNNEL] in log; geo via check_target_stability)",
    ]
    for row in domain_analysis.get("tunnel_domains") or []:
        if not isinstance(row, dict):
            continue
        pending = ", ".join(row.get("matched_pending_ips") or []) or "none"
        domain = str(row.get("domain") or "")
        cdn_note = (
            " [CDN/resource candidate: if E2006/E2002 and log shows -1 -1 1 on this domain, "
            "trial one direct_patterns entry on next retry]"
            if _cdn_resource_domain_hint(domain)
            else ""
        )
        lines.append(
            f"- {domain}: geo={row.get('geo_summary')}, "
            f"is_china={row.get('is_china')}, pending_ip={pending}{cdn_note}",
        )

    lines.extend(["", "## direct_domains ([SNI-DIRECT]; resource/channel only for direct_patterns)"])
    for row in domain_analysis.get("direct_domains") or []:
        if isinstance(row, dict):
            lines.append(f"- {row.get('domain')}")

    lines.extend(["", "## unknown_domains (no SNI line in log; do not bulk direct)"])
    for row in domain_analysis.get("unknown_domains") or []:
        if isinstance(row, dict):
            lines.append(f"- {row.get('domain')}")

    unmatched = domain_analysis.get("unmatched_pending_ips") or []
    if unmatched:
        lines.extend(["", "## unmatched_pending_ips (PENDING in log, IP not aligned to tunnel resolve)"])
        lines.extend(f"- {ip}" for ip in unmatched)

    non_china = domain_analysis.get("non_china_domains") or []
    if non_china:
        lines.extend(["", "## non_china_domains"])
        lines.extend(f"- {d}" for d in non_china)

    errors = domain_analysis.get("errors") or []
    if errors:
        lines.extend(["", "## errors"])
        lines.extend(f"- {e}" for e in errors)

    console_eq = domain_analysis.get("console_equivalent") or []
    if console_eq:
        lines.extend(["", "## console_equivalent (shell script stdout summary)"])
        lines.extend(console_eq[:80])
        if len(console_eq) > 80:
            lines.append(f"... {len(console_eq)} lines total; see JSON file")

    log_text = _read_source_log_text(domain_analysis)
    ipv6_lines = _extract_ipv6_rule_direct_lines(log_text)
    if ipv6_lines:
        lines.extend(
            [
                "",
                "## log_ipv6_rule_direct (no SNI — invisible in domain list; PCAP if E2006/E2002)",
            ],
        )
        lines.extend(f"- {line}" for line in ipv6_lines)
        if not domain_analysis.get("unknown_domains"):
            lines.append(
                "- Note: unknown_domains empty but IPV6-RULE direct traffic exists — "
                "possible server-list/API path bypassing domain extractor."
            )

    lines.extend(
        [
            "",
            "## Full JSON",
            json.dumps(domain_analysis, ensure_ascii=False, indent=2),
        ],
    )
    return "\n".join(lines)


def extract_domain_region_from_log(
    log_path: Path,
    *,
    output_path: Path | None = None,
    port: int = 443,
) -> DomainRegionAnalysis:
    """分析日志并可选写入 JSON 文件。"""
    extractor = GameTurboLogDomainExtractor(port=port)
    result = extractor.analyze_log_file(log_path)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.to_json_text(), encoding="utf-8")
        logger.info("Domain/region analysis written: %s", output_path)
    return result


def load_domain_region_analysis_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Cannot parse %s: %s", path, exc)
        return None
