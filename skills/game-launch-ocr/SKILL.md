---
name: game-launch-ocr
description: >-
  Executor-phase guide: generic mobile game login via OCR + tap; no per-game scripts.
  Path: skills/game-launch-ocr/SKILL.md
---

# Generic game login flow (OCR + adb tap)

**Goal:** start process `game.package_name`. **Not** judging in-game main scene (observer phase).

Classify **current stage** from OCR, then act. Prefer **live OCR** over old learned skills.

## OCR format

`- (x, y) 'text' (confidence)` → tap **(x, y)**. After taps use `get_ocr_summary` or `tap_and_observe`; do not reuse round-opening OCR.

## Stage model (typical order; may skip/loop)

| Stage ID | Typical OCR / screen | Actions |
|----------|----------------------|---------|
| `splash` | splash, logo, health advisory, loading, % | `wait_seconds` 1–3s; re-OCR if no button |
| `system_permission` | Allow, Deny, storage, phone | tap **Allow/Agree**; prefer `tap_and_observe` |
| `privacy` | terms, privacy, I agree, checkbox | check box if needed, then **Agree/Accept/Enter** |
| `announcement` | notice, event, Close, ×, skip | **Close / Got it / Skip**; corner or bottom CTA on fullscreen |
| `login` | login, guest, one-tap, phone, OTP, WeChat/QQ | prefer **guest/one-tap**; else OCR center → `fill_credential_field(...,'username')` → `...'password'` → tap **Login** |
| `server_select` | server list, Enter game, Start | tap **Enter/Start/OK**; pick recommended or first row |
| `download` | download, update, unzip, % | longer `wait_seconds`; swipe/wait if stuck; process may still be absent |
| `unknown` | unclassified | `get_ocr_summary`; `press_back` or `open_game_app` if stuck |

Order varies (announcement before privacy, etc.); follow OCR, not the table top-to-bottom.

## Standard loop

1. Controller already `open_game_app` / `am start`; `wait_seconds` ~2s.
2. `get_ocr_summary` → **stage ID** → `tap_and_observe` safest control.
3. Repeat until load/login triggered.
4. **`wait_for_game_running(summary)`** (state last stage, e.g. "tapped Enter after announcement").
5. On success stop tapping; on failure `report_flow_done(success=false, summary=stage+OCR).

## Stage notes

### Privacy / terms
- Agree, Accept, I know, Enter game, checkbox+confirm.
- If both Reject and Agree, only **Agree**.
- Long scroll: bottom primary button, not body text.

### Announcements
- Close, ×, skip, got it, claim-then-close.
- Carousel: close or bottom if corner fails.

### Login
- Prefer guest / one-tap / quick start.
- Account/password: `fill_credential_field` per field (username then password); tool clears then fills from credentials.yaml.
- WeChat/QQ icons: tap and wait for redirect.
- Stuck: protocol checkbox; `wait_seconds` then re-OCR.

### Server select
- Enter game, Start adventure, OK equivalents.
- List: recommended / new server or first item.

### Download / update
- Mostly wait; optional wait inside `executor.ad_initial_wait_s`.
- Downloading ≠ failure; process may not exist yet.

## Tools

| Tool | Use |
|------|-----|
| `open_game_app` | not game foreground; no monkey |
| `tap_coordinate` / `tap_and_observe` | OCR coords |
| `swipe_screen` | server list, long terms |
| `press_back` | wrong sub-page; may exit game |
| `wait_seconds` | animation; **not** a substitute for `wait_for_game_running` |
| `wait_for_game_running` | end of login chain |
| `read_login_flow_guide` | re-read this file |
| `credentials_status` | credentials file OK? |
| `fill_credential_field` | clear + fill username/password |
| `list_learned_skills` / `read_learned_skill` | optional history |

CLI tap (same as tool): `python -m game_agent.tools.adb_tap X Y [-s SERIAL]`

## Common mistakes

- Tap Reject/exit → no process; `open_game_app` again.
- Stale OCR → repeated wrong taps.
- Tap after process up → violates executor/observer boundary.
- Demand in-game/character-create in executor → only need **pidof success**.

## Reporting

Each round: **stage ID** + next tools. `wait_for_game_running` summary must describe last meaningful tap (e.g. "closed notice then Enter game").
