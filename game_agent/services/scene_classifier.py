"""登录后场景分类：规则优先，不依赖逐步 VLM。"""

from __future__ import annotations

import re
from pathlib import Path

from game_agent.models.launch_graph_state import LaunchFacts
from game_agent.models.scene import SceneClassification, SceneTransition
from game_agent.services.dialogue_heuristics import score_dialogue_from_bboxes
from game_agent.utils.in_game_hud_ocr import match_in_game_hud_ocr, should_trigger_in_game_hud_check
from game_agent.utils.ocr_util import OcrBbox, is_screencap_mostly_black

_SKIP_RE = re.compile(r"跳过|Skip", re.IGNORECASE)
_CONTINUE_RE = re.compile(r"继续|Continue|下一步|点击继续|点击屏幕", re.IGNORECASE)
_CONFIRM_RE = re.compile(r"^(确定|确认|OK|Agree)$", re.IGNORECASE)
_TUTORIAL_RE = re.compile(r"点击|引导|教程|tutorial|手指|轻触", re.IGNORECASE)
_LOADING_RE = re.compile(
    r"加载|loading|请稍候|正在进入|连接中|载入",
    re.IGNORECASE,
)
_ENTER_WORLD_RE = re.compile(
    r"进入世界|Enter\s*World|进入游戏|开始游戏|Click\s*to\s*Create|创建角色|创角",
    re.IGNORECASE,
)
_CHAR_SLOT_RE = re.compile(r"LV\.|等级|Lv\.|选择角色|已有角色", re.IGNORECASE)
_NARRATIVE_RE = re.compile(r"[\u4e00-\u9fff]{6,}")

_LOW_CONFIDENCE_THRESHOLD = 0.4
_SCENE_CHANGE_CONFIDENCE = 0.6


def _bottom_bboxes(bboxes: list[OcrBbox], screen_h: int, *, ratio: float = 0.55) -> list[OcrBbox]:
    if screen_h <= 0:
        return list(bboxes)
    cutoff = int(screen_h * ratio)
    return [b for b in bboxes if b.cy >= cutoff]


def compute_scene_fingerprint(
    scene_id: str,
    *,
    ocr_summary: str,
    bboxes: list[OcrBbox],
    screen_h: int,
) -> str:
    if scene_id == "dialogue":
        bottom = _bottom_bboxes(bboxes, screen_h)
        texts = sorted((b.text or "").strip() for b in bottom if (b.text or "").strip())
        head = "|".join(texts[:10])
        return f"dialogue|{head}"[:320]
    if scene_id == "loading":
        return f"loading|{(ocr_summary or '')[:120]}"
    if scene_id == "tutorial":
        return f"tutorial|{(ocr_summary or '')[:200]}"
    return f"{scene_id}|{(ocr_summary or '')[:240]}"


def _score_dialogue(
    bboxes: list[OcrBbox],
    ocr_summary: str,
    screen_h: int,
    facts: LaunchFacts,
) -> tuple[float, str]:
    merged = ocr_summary or ""
    if facts.login_blocking or facts.sub_account_blocking:
        return 0.0, ""
    if facts.initial_privacy_dialog or facts.announcement_overlay:
        return 0.0, ""
    if facts.character_creation_blocking:
        return 0.0, ""
    if match_in_game_hud_ocr(merged):
        return 0.0, ""
    if _ENTER_WORLD_RE.search(merged) and _CHAR_SLOT_RE.search(merged):
        return 0.0, ""

    score, evidence = score_dialogue_from_bboxes(bboxes, screen_h=screen_h)
    if score <= 0.0:
        return 0.0, ""

    extras: list[str] = []
    if _SKIP_RE.search(merged) or _CONTINUE_RE.search(merged):
        score = min(1.0, score + 0.08)
        extras.append("dialogue_cta_optional")
    if _CONFIRM_RE.search(merged):
        score = min(1.0, score + 0.05)
        extras.append("confirm_btn")
    if extras:
        evidence = f"{evidence},{','.join(extras)}" if evidence else ",".join(extras)
    return score, evidence


def _score_tutorial(ocr_summary: str, dialogue_score: float) -> tuple[float, str]:
    merged = ocr_summary or ""
    if dialogue_score >= 0.65:
        return 0.0, ""
    if not _TUTORIAL_RE.search(merged):
        return 0.0, ""
    score = 0.55
    evidence = ["tutorial_hint"]
    if _CONTINUE_RE.search(merged):
        score += 0.15
        evidence.append("continue")
    return min(score, 0.85), ",".join(evidence)


def classify_scene(
    facts: LaunchFacts,
    bboxes: list[OcrBbox],
    *,
    ocr_summary: str = "",
    screen_w: int = 0,
    screen_h: int = 0,
    screenshot_path: str | Path | None = None,
) -> SceneClassification:
    """规则分类当前场景；高价值场景优先于 unknown。"""
    merged = ocr_summary or ""
    _ = screen_w

    if facts.announcement_overlay:
        fp = compute_scene_fingerprint("blocking_popup", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h)
        return SceneClassification(
            scene_id="blocking_popup",
            confidence=0.9,
            evidence="announcement_overlay",
            fingerprint=fp,
            source="rule",
        )

    hud_trigger, hud_hits = should_trigger_in_game_hud_check(merged)
    if hud_trigger:
        fp = compute_scene_fingerprint("in_game_hud", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h)
        return SceneClassification(
            scene_id="in_game_hud",
            confidence=0.85,
            evidence=f"hud={','.join(hud_hits[:4])}",
            fingerprint=fp,
            source="rule",
        )

    if facts.character_creation_blocking or facts.interpreter_stage == "character_creation":
        fp = compute_scene_fingerprint(
            "character_creation", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h
        )
        return SceneClassification(
            scene_id="character_creation",
            confidence=0.8,
            evidence="character_creation_blocking",
            fingerprint=fp,
            source="rule",
        )

    if _CHAR_SLOT_RE.search(merged) and _ENTER_WORLD_RE.search(merged):
        fp = compute_scene_fingerprint(
            "character_select", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h
        )
        return SceneClassification(
            scene_id="character_select",
            confidence=0.75,
            evidence="char_slot+enter",
            fingerprint=fp,
            source="rule",
        )

    mostly_black = False
    if screenshot_path:
        mostly_black = is_screencap_mostly_black(screenshot_path)

    if facts.download_visible or facts.sub_account_blocking or facts.login_blocking:
        pass  # business facts handled by static nodes, not scene loading
    elif facts.server_slot_visible:
        pass
    elif facts.initial_privacy_dialog or facts.terms_checkbox_visible:
        pass
    elif mostly_black or (
        _LOADING_RE.search(merged) and len(bboxes) <= 6
    ):
        conf = 0.9 if mostly_black else 0.7
        fp = compute_scene_fingerprint("loading", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h)
        return SceneClassification(
            scene_id="loading",
            confidence=conf,
            evidence="loading_or_black" if mostly_black else "loading_text",
            fingerprint=fp,
            source="rule",
        )

    dialogue_score, dialogue_ev = _score_dialogue(bboxes, merged, screen_h, facts)
    if dialogue_score >= 0.55:
        fp = compute_scene_fingerprint("dialogue", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h)
        return SceneClassification(
            scene_id="dialogue",
            confidence=dialogue_score,
            evidence=dialogue_ev,
            fingerprint=fp,
            source="rule",
        )

    tutorial_score, tutorial_ev = _score_tutorial(merged, dialogue_score)
    if tutorial_score >= 0.55:
        fp = compute_scene_fingerprint("tutorial", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h)
        return SceneClassification(
            scene_id="tutorial",
            confidence=tutorial_score,
            evidence=tutorial_ev,
            fingerprint=fp,
            source="rule",
        )

    fp = compute_scene_fingerprint("unknown", ocr_summary=merged, bboxes=bboxes, screen_h=screen_h)
    return SceneClassification(
        scene_id="unknown",
        confidence=0.0,
        evidence="no_rule_match",
        fingerprint=fp,
        source="rule",
    )


def detect_scene_transition(
    *,
    prev_scene_id: str,
    prev_fingerprint: str,
    classification: SceneClassification,
    facts: LaunchFacts,
    ocr_summary: str,
    screenshot_path: str | Path | None = None,
) -> SceneTransition:
    """事件驱动场景迁移检测；不因点击次数退出。"""
    new_id = classification.scene_id
    merged = ocr_summary or ""

    if screenshot_path and is_screencap_mostly_black(screenshot_path):
        return SceneTransition(
            kind="animation_or_loading",
            reason="mostly_black_frame",
            from_scene=prev_scene_id,
            to_scene=new_id,
        )

    if facts.vision_has_anomaly and facts.vision_anomaly_reason:
        return SceneTransition(
            kind="blocking_popup",
            reason=facts.vision_anomaly_reason[:120],
            from_scene=prev_scene_id,
            to_scene=new_id,
        )

    hud_trigger, _ = should_trigger_in_game_hud_check(merged)
    if hud_trigger or new_id == "in_game_hud":
        return SceneTransition(
            kind="exit_to_game",
            reason="hud_detected",
            from_scene=prev_scene_id,
            to_scene=new_id,
        )

    if new_id in ("blocking_popup", "character_creation", "character_select"):
        return SceneTransition(
            kind="scene_changed",
            reason=f"to_{new_id}",
            from_scene=prev_scene_id,
            to_scene=new_id,
        )

    if (
        prev_scene_id
        and prev_scene_id != "unknown"
        and new_id != prev_scene_id
        and classification.confidence >= _SCENE_CHANGE_CONFIDENCE
    ):
        return SceneTransition(
            kind="scene_changed",
            reason=f"{prev_scene_id}->{new_id}",
            from_scene=prev_scene_id,
            to_scene=new_id,
        )

    if classification.confidence < _LOW_CONFIDENCE_THRESHOLD and prev_scene_id in (
        "dialogue",
        "tutorial",
        "loading",
    ):
        return SceneTransition(
            kind="low_confidence",
            reason="cannot_confirm_scene",
            from_scene=prev_scene_id,
            to_scene=new_id,
        )

    if (
        prev_fingerprint
        and classification.fingerprint
        and prev_fingerprint != classification.fingerprint
        and new_id == prev_scene_id
    ):
        return SceneTransition(kind="none", reason="fingerprint_progress", from_scene=prev_scene_id, to_scene=new_id)

    return SceneTransition(kind="none", reason="", from_scene=prev_scene_id, to_scene=new_id)
