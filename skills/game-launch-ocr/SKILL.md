---
name: game-launch-ocr
description: >-
  Executor-phase guide: launch → login → in-game via OCR + tap. Popups: prefer Agree/Accept;
  first-launch privacy; download-size dialogs — wait after consent. Path: skills/game-launch-ocr/SKILL.md
---

# Generic game login flow (OCR + adb tap)

**Goal:** full chain **launch → login → in-game** via OCR + tap; confirm with `check_in_game` (multimodal). GameTurbo logs run in parallel from launch.

Classify **current stage** from **live OCR**, then act. Prefer `tap_and_observe` on dialog buttons.

---

## Golden rules (popups & consent)

1. **When in doubt on a dialog, tap the positive / continue side** — not exit, not “later”, not “reject”, unless OCR clearly shows no Agree path.
2. **First cold start often shows privacy / terms** (`privacy` stage) **before** login — do not skip; find **Agree / Accept / 同意 / 接受 / 我知道了 / 进入游戏** and tap it (`tap_and_observe`).
3. **Checkbox first** if OCR shows unchecked protocol box next to Agree — tap checkbox, then Agree.
4. **Download / update popups** may appear **during** `download` stage (“需要下载 XX MB”, “资源更新”, “确认下载”) — tap **确认 / 下载 / 继续 / OK / Agree**, then **`wait_seconds` 3–8s** and re-OCR; **do not** treat the dialog as failure.
5. After any consent tap, **always** `get_ocr_summary` or `tap_and_observe` — do not assume the screen advanced.
6. **Parallel monitors** only fail-fast on **network** errors — a download consent popup is **not** a network failure; keep progressing.

### Positive vs negative controls (OCR keywords)

| Prefer tap (continue) | Avoid unless no alternative |
|----------------------|-----------------------------|
| 同意, 接受, 确认, 继续, 确定, OK, Agree, Accept, Allow, Got it, 知道了, 进入, 开始, 下载, Download, Update now | 拒绝, 不同意, 取消, Cancel, 稍后, Later, 退出, Exit, 关闭 (only if clearly dismisses **announcement**, not privacy) |
| 游客, 一键, Guest, Quick start | Reject, Deny (permissions) |

If **both** Agree and Reject visible → **only Agree**.

---

## OCR format

`- (x, y) 'text' (confidence)` → tap **(x, y)**. After every tap use `get_ocr_summary` or `tap_and_observe`; do not reuse round-opening OCR.

---

## Stage model (typical order; may skip/loop)

| Stage ID | Typical OCR / screen | Actions |
|----------|----------------------|---------|
| `splash` | logo, health advisory, loading, % | `wait_seconds` 1–3s; re-OCR |
| `system_permission` | Allow, Deny, 允许, 存储, 电话 | tap **Allow / 允许** (`tap_and_observe`) |
| `privacy` | 隐私, 用户协议, terms, checkbox, **同意** | checkbox if needed → **Agree/Accept/进入**; scroll terms = tap **bottom** primary button |
| `announcement` | 公告, 活动, Close, ×, 跳过 | **Close / 知道了 / 跳过** — corner or bottom CTA |
| `login` | 登录, 游客, 一键, 手机, 微信/QQ | **guest/one-tap** first; else credentials + Login |
| `server_select` | 选服, 服务器, 进入游戏, 开始冒险 | tap recommended row then **进入/开始/确定** |
| `download` | 下载, 更新, MB, GB, %, 解压, 资源包 | see **Download stage** below |
| `unknown` | unclassified dialog | `get_ocr_summary`; if modal with Agree → tap Agree |

Order varies (privacy before or after announcement); **trust OCR**, not a fixed script.

---

## Standard loop

1. **`wait_for_package_installed` once** after deploy (do not recheck install).
2. **`open_game_app`**, then `wait_seconds` ~2s.
3. `get_ocr_summary` → **stage ID** → act (usually `tap_and_observe` on primary CTA).
4. Repeat through login / server / download.
5. **`wait_for_game_running`** once if process may still be absent (milestone only).
6. After server select, long download, or likely HUD: **`check_in_game`** until consecutive confirmations.
7. On `check_in_game` confirmed → stop tapping. Blocker only: `report_flow_done(success=false, ...)`.

---

## Stage notes (detailed)

### First launch — privacy / terms (`privacy`)

- **Very common** on first open after install: full-screen or bottom-sheet terms.
- Steps: `get_ocr_summary` → if 隐私/协议/用户协议 → stage `privacy`.
- If checkbox OCR visible and unchecked → tap checkbox center, then tap **同意/接受/Agree**.
- Long scrollable text: swipe up if needed, then tap **bottom** colored button (not paragraph text).
- After tap: `tap_and_observe` or `wait_seconds` 2s + `get_ocr_summary` before assuming login screen.

### Permissions (`system_permission`)

- Android dialogs: **Allow / 允许** only.

### Announcements (`announcement`)

- Marketing / patch notes: **Close / × / 跳过 / 知道了** — not the same as privacy Agree, but still dismiss to continue.

### Login (`login`)

- Prefer **游客登录 / 一键登录 / guest / quick start**.
- Account/password: OCR field centers → `fill_credential_field(username)` → `fill_credential_field(password)` → tap **登录/Login**.
- Protocol checkbox on login page: tap checkbox before Login if OCR shows it.

### Server select (`server_select`)

- Tap **推荐 / 新服** row or first row, then **进入游戏 / 开始冒险 / 确定**.

### Download / update (`download`) — important

This stage is **mostly waiting**, but games often show **extra consent popups**:

| Popup pattern (OCR) | What to do |
|---------------------|------------|
| “需要下载 XXX MB” / “资源大小” / “确认下载” / “更新包” | Tap **确认 / 下载 / 继续 / OK** → `wait_seconds` 5–15s → `get_ocr_summary` |
| Progress % / 正在下载 / 解压中 | **Do not spam tap** — `wait_seconds` 3–8s, re-OCR until gone or HUD appears |
| “WLAN only” / 移动网络提示 | Tap **继续下载 / 仍要下载 / 确定** (agree to download) |
| No progress for many rounds | `wait_seconds` longer; optional `check_in_game`; do not `report_flow_done` for slow CDN alone |

- Downloading ≠ test failure; game process may appear late.
- After large download, call **`check_in_game`** when HUD / main UI likely.

---

## Tools

| Tool | Use |
|------|-----|
| `wait_for_package_installed` | once after deploy |
| `open_game_app` | after package ready |
| `get_ocr_summary` / `tap_and_observe` | every decision; prefer observe after taps |
| `tap_coordinate` | when observe not needed |
| `swipe_screen` | long terms, server list |
| `press_back` | wrong sub-page only — may exit game |
| `wait_seconds` | animations, download progress (not a substitute for `wait_for_game_running`) |
| `wait_for_game_running` | process milestone |
| `check_in_game` | required to finish |
| `read_login_flow_guide` | re-read this skill |
| `credentials_status` / `fill_credential_field` | account login |
| `report_flow_done(success=false)` | unrecoverable blocker only |

---

## Common mistakes

- Tap **拒绝 / Cancel / 不同意** on privacy or download consent → stuck or app exits.
- Ignore privacy on first launch → login buttons blocked behind terms.
- Close download-size dialog with back → download never starts.
- Spam tap during % progress → mis-clicks.
- Stale OCR → wrong stage; always refresh after each action.
- Stop after `wait_for_game_running` → still need login/server/download/`check_in_game`.

---

## Reporting

Each round: **stage ID** + next tools (e.g. `privacy` → `tap_and_observe` on 同意 at (x,y)).

`wait_for_game_running` summary must describe last meaningful action (e.g. "tapped 确认下载 on 120MB update dialog").
