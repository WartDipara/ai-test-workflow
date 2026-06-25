"""从 OCR bbox + 可选多模态分类当前屏幕事实。"""

from __future__ import annotations

import json
import re

from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.screen_interpretation import ScreenInterpretation
from game_agent.services.enter_gate_planner import enter_gate_likely_visible
from game_agent.services.login_stage_probe import probe_login_stage
from game_agent.services.server_selector_locator import find_enter_game_bbox, locate_server_selector_target
from game_agent.utils.ocr_util import OcrBbox

_PRIVACY_CONTEXT_RE = re.compile(
    r"个人信息保护|隐私政策|用户协议|许可及服务|已阅读并同意|protect.*privacy|privacy\s*policy",
    re.IGNORECASE,
)
from game_agent.services.download_gate import ocr_has_download_context
from game_agent.graphs.launch_state_store import completed_tree_node, is_privacy_checked
from game_agent.services.privacy_gate import privacy_modal_still_open
_ANNOUNCEMENT_RE = re.compile(
    r"公告|announcement|Notice|日常通知|点击空白|今日不再|不再提示",
    re.IGNORECASE,
)
_OVERLAY_HINT_RE = re.compile(r"Notice|日常通知", re.IGNORECASE)
_SUB_ACCOUNT_HINT_RE = re.compile(
    r"sub-?account|小号|子账号|选择小号|上次登录",
    re.IGNORECASE,
)
_CHARACTER_HINT_RE = re.compile(
    r"创角|创建角色|选择职业|Click\s*to\s*Create|Create\s*Role|Enter\s*World|进入世界|LV\.",
    re.IGNORECASE,
)
_ENTER_WORLD_OCR_RE = re.compile(r"Enter\s*World|进入世界", re.IGNORECASE)
_PK_AGREEMENT_RE = re.compile(r"PK\s*玩法|接受.*PK", re.IGNORECASE)


def _looks_like_character_select_screen(
    interp: ScreenInterpretation,
    *,
    ocr_merged: str = "",
) -> bool:
    """选角界面常被误判为 sub_account_select；用 OCR/信号纠偏。"""
    merged = ocr_merged or ""
    signals = " ".join(interp.completion_signals or [])
    tap_label = interp.tap_target.label if interp.tap_target else ""
    blob = f"{merged} {signals} {tap_label}"
    if _ENTER_WORLD_OCR_RE.search(blob) and _CHARACTER_HINT_RE.search(blob):
        return True
    if re.search(r"LV\.\d", blob, re.IGNORECASE) and _ENTER_WORLD_OCR_RE.search(blob):
        return True
    return False


def classify_screen_facts(
    bboxes: list[OcrBbox],
    *,
    screen_w: int,
    screen_h: int,
    ocr_summary: str = "",
) -> LaunchFacts:
    """把 OCR 结果转为 LaunchFacts。"""
    login_probe = probe_login_stage(bboxes, screen_w=screen_w, screen_h=screen_h)
    enter_anchor = find_enter_game_bbox(bboxes, screen_h=screen_h)
    target, _enter = locate_server_selector_target(bboxes, screen_w=screen_w, screen_h=screen_h)

    merged = ocr_summary or " ".join(b.text for b in bboxes)
    enter_visible = enter_gate_likely_visible(bboxes, ocr_merged=merged)
    download_visible = ocr_has_download_context(merged)
    announcement_overlay = bool(_ANNOUNCEMENT_RE.search(merged))
    privacy_context = bool(_PRIVACY_CONTEXT_RE.search(merged))

    sub_action = None
    sub_label = ""
    if login_probe.action_xy is not None:
        sub_action = login_probe.action_xy
        sub_label = login_probe.action_label

    reason_parts = [login_probe.reason]
    if enter_visible:
        reason_parts.append("enter_gate_visible")
    if enter_anchor is not None:
        reason_parts.append(f"enter_anchor={enter_anchor.text[:40]!r}")
    if privacy_context:
        reason_parts.append("privacy_context_detected")
    if target is not None:
        reason_parts.append(f"server_slot={target.label[:40]!r}")
    if _CHARACTER_HINT_RE.search(merged):
        reason_parts.append("character_creation_ocr")

    return LaunchFacts(
        login_blocking=login_probe.blocking and login_probe.stage == "login_form",
        login_stage=login_probe.stage,
        sub_account_blocking=login_probe.blocking and login_probe.stage == "sub_account_select",
        sub_account_action_xy=sub_action,
        sub_account_label=sub_label,
        enter_cta_visible=enter_visible,
        enter_cta_xy=None,
        enter_cta_label=enter_anchor.text.strip() if enter_anchor else "",
        server_slot_visible=(
            target is not None and target.source == "ocr"
        ),
        download_visible=download_visible,
        announcement_overlay=announcement_overlay,
        character_creation_blocking=bool(_CHARACTER_HINT_RE.search(merged)),
        classify_reason="; ".join(reason_parts),
    )


def _parse_vision_analyze_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def merge_vision_into_facts(facts: LaunchFacts, vision_raw: str) -> LaunchFacts:
    """多模态画面解释补充 OCR facts（OCR 已明确时优先保留 OCR）。"""
    data = _parse_vision_analyze_json(vision_raw)
    stage = str(data.get("stage", "") or "").strip().lower()
    has_anomaly = bool(data.get("has_anomaly", False))
    anomaly_reason = str(data.get("anomaly_reason", "") or "")[:300]
    progress = str(data.get("progress", "") or "")

    updates: dict = {
        "vision_stage": stage,
        "vision_has_anomaly": has_anomaly,
        "vision_anomaly_reason": anomaly_reason,
    }

    if stage == "resource_download" and not facts.login_blocking and not facts.initial_privacy_dialog:
        updates["download_visible"] = True
    if stage == "login" and not facts.sub_account_blocking:
        if not facts.enter_cta_visible or facts.login_stage == "login_form":
            updates["login_blocking"] = True
            updates["login_stage"] = "login_form"
    if stage == "enter_game" and not facts.enter_cta_visible:
        updates["enter_cta_visible"] = True
    if stage == "character_creation":
        updates["character_creation_blocking"] = True
    if stage == "announcement":
        updates["announcement_overlay"] = True

    reason = facts.classify_reason
    if stage:
        reason = f"{reason}; vision_stage={stage}"
    if progress:
        reason = f"{reason}; vision_progress={progress}"
    if has_anomaly and anomaly_reason:
        reason = f"{reason}; vision_anomaly={anomaly_reason[:80]}"
    updates["classify_reason"] = reason

    return facts.model_copy(update=updates)


def merge_interpretation_into_facts(
    facts: LaunchFacts,
    interp: ScreenInterpretation,
    *,
    ocr_has_sub_account_coords: bool = False,
    ocr_merged: str = "",
) -> LaunchFacts:
    """
    将 ScreenInterpreter 结果合并进 LaunchFacts。
    OCR 已有明确坐标时保留 OCR（tap_target 仅填补空缺）。
    """
    stage = (interp.stage or "unknown").strip().lower()
    updates: dict = {
        "interpreter_stage": stage,
        "interpreter_reason": (interp.reason or "")[:300],
        "screen_completion_signals": list(interp.completion_signals),
    }
    reason = facts.classify_reason
    if stage:
        reason = f"{reason}; interpreter_stage={stage}"
    if interp.reason:
        reason = f"{reason}; interpreter={interp.reason[:80]}"

    tap = interp.tap_target
    tap_xy = (tap.x, tap.y) if tap is not None and tap.x > 0 and tap.y > 0 else None
    tap_label = tap.label if tap else ""

    if stage in ("sub_account_select", "sub_account"):
        if _looks_like_character_select_screen(interp, ocr_merged=ocr_merged):
            updates["character_creation_blocking"] = True
            updates["sub_account_blocking"] = False
            updates["login_stage"] = "clear"
            if tap_xy is not None:
                updates.pop("sub_account_action_xy", None)
                updates.pop("sub_account_label", None)
        elif interp.blocking:
            updates["sub_account_blocking"] = True
            updates["login_stage"] = "sub_account_select"
            if tap_xy is not None and not ocr_has_sub_account_coords:
                updates["sub_account_action_xy"] = tap_xy
                if tap_label:
                    updates["sub_account_label"] = tap_label

    elif stage == "login":
        if interp.blocking and not facts.sub_account_blocking:
            updates["login_blocking"] = True
            updates["login_stage"] = "login_form"

    elif stage == "server_select":
        if tap_xy is not None:
            updates["enter_cta_visible"] = True
            if tap_label:
                updates["enter_cta_label"] = tap_label

    elif stage == "announcement":
        if interp.blocking:
            updates["announcement_overlay"] = True
        if tap_xy is not None:
            updates["announcement_dismiss_xy"] = tap_xy

    elif stage == "character_creation":
        if interp.blocking:
            updates["character_creation_blocking"] = True

    elif stage == "resource_download":
        if interp.blocking:
            updates["download_visible"] = True

    updates["classify_reason"] = reason
    return facts.model_copy(update=updates)


def needs_sync_interpretation(facts: LaunchFacts, *, ocr_merged: str = "") -> bool:
    """L2：阻塞但缺少可执行坐标，或阶段未知需模型判读。"""
    if facts.terms_checkbox_visible:
        return False
    if facts.download_visible:
        return False
    if facts.sub_account_blocking and facts.sub_account_action_xy is not None:
        return False
    if facts.sub_account_blocking and facts.sub_account_action_xy is None:
        return True
    if (
        not facts.sub_account_blocking
        and ocr_merged
        and _SUB_ACCOUNT_HINT_RE.search(ocr_merged)
        and not facts.login_blocking
    ):
        return True
    if facts.announcement_overlay and facts.announcement_dismiss_xy is None:
        return True
    if (
        ocr_merged
        and _OVERLAY_HINT_RE.search(ocr_merged)
        and facts.announcement_dismiss_xy is None
        and not facts.login_blocking
    ):
        return True
    if facts.character_creation_blocking:
        return True
    if ocr_merged and _CHARACTER_HINT_RE.search(ocr_merged) and not facts.character_creation_blocking:
        return True
    if facts.login_blocking and facts.login_stage == "login_form":
        return False
    if facts.server_slot_visible and facts.enter_cta_visible:
        return False
    return False


def interpretation_focus_for_facts(facts: LaunchFacts) -> str:
    if facts.sub_account_blocking and facts.sub_account_action_xy is None:
        return "sub-account picker: pick existing account row to tap, not create/purchase"
    if facts.announcement_overlay:
        return "dismiss announcement/event popup; tap close button or blank area outside panel"
    if facts.character_creation_blocking:
        return "character creation flow"
    return "launch screen routing"


def needs_async_vision_enrichment(facts: LaunchFacts) -> bool:
    """OCR 已能路由的页面不提交后台多模态；歧义/公告场景才 enrich。"""
    if facts.initial_privacy_dialog:
        return False
    if facts.login_blocking:
        return False
    if facts.sub_account_blocking:
        return False
    if facts.terms_checkbox_visible:
        return False
    if facts.enter_cta_visible or facts.server_slot_visible:
        return False
    if facts.download_visible:
        return False
    if facts.announcement_overlay:
        return True
    return False


def merge_analyze_screen_response(
    facts: LaunchFacts,
    analyze_response_json: str,
) -> tuple[LaunchFacts, str]:
    """解析 run_analyze_screen 的 JSON 回调，合并进 facts 并返回简短 hint。"""
    try:
        payload = json.loads(analyze_response_json)
    except json.JSONDecodeError:
        return facts, "analyze_screen: invalid JSON response"

    if int(payload.get("errorCode", -1)) != 0:
        msg = str(payload.get("errorMessage", "") or "analyze_screen failed")[:200]
        return facts, msg

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return facts, "analyze_screen: empty data"

    vision_raw = json.dumps(
        {
            "has_anomaly": data.get("has_anomaly", False),
            "anomaly_reason": data.get("anomaly_reason", ""),
            "stage": data.get("stage", "unknown"),
            "progress": data.get("progress", ""),
        },
        ensure_ascii=False,
    )
    merged = merge_vision_into_facts(facts, vision_raw)
    hint_parts = [f"vision_stage={merged.vision_stage}"]
    if merged.vision_has_anomaly and merged.vision_anomaly_reason:
        hint_parts.append(f"anomaly={merged.vision_anomaly_reason[:80]}")
    progress = str(data.get("progress", "") or "")
    if progress:
        hint_parts.append(f"progress={progress[:40]}")
    return merged, "; ".join(hint_parts)


def merge_sticky_gate_facts(
    facts: LaunchFacts,
    *,
    prev_facts: LaunchFacts | None,
    state: dict | None = None,
) -> LaunchFacts:
    """
    classify 每轮重建 facts 时，保留未完成里程碑的门禁信号，避免 OCR 冲掉隐私弹窗状态。
    """
    if prev_facts is None:
        return facts

    privacy_done = False
    if state is not None:
        privacy_done = completed_tree_node(state, "handle_initial_privacy_dialog") and is_privacy_checked(
            state  # type: ignore[arg-type]
        )

    updates: dict = {}
    ocr = str(state.get("last_ocr_summary") or "") if state is not None else ""
    if not privacy_done:
        if privacy_modal_still_open(ocr) and not facts.initial_privacy_dialog:
            updates["initial_privacy_dialog"] = True
            updates["privacy_gate_kind"] = "modal"
        if prev_facts.initial_privacy_dialog and not facts.initial_privacy_dialog:
            if prev_facts.privacy_gate_kind == "modal" or prev_facts.agree_button_xy:
                updates["initial_privacy_dialog"] = True
                updates["privacy_gate_kind"] = prev_facts.privacy_gate_kind or "modal"
        if prev_facts.agree_button_xy and facts.agree_button_xy is None:
            if updates.get("initial_privacy_dialog") or prev_facts.initial_privacy_dialog:
                updates["agree_button_xy"] = prev_facts.agree_button_xy

    # 误设的 download_visible 在隐私屏上应被抑制
    if (
        (updates.get("initial_privacy_dialog") or facts.initial_privacy_dialog or prev_facts.initial_privacy_dialog)
        and facts.download_visible
        and not facts.download_in_progress
        and not facts.download_progress_text
    ):
        updates["download_visible"] = False
        updates["download_in_progress"] = False
        updates["download_gate_kind"] = ""

    if not updates:
        return facts
    reason = facts.classify_reason
    reason = f"{reason}; sticky_gate_merge" if reason else "sticky_gate_merge"
    updates["classify_reason"] = reason
    return facts.model_copy(update=updates)
