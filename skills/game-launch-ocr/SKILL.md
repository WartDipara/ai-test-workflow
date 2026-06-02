---
name: game-launch-ocr
description: >-
  执行者阶段通用规范：多款游戏登录流程大同小异，按 OCR 识别阶段并 tap；
  不写 per-game 脚本，由 AI 主脑决策。路径：skills/game-launch-ocr/SKILL.md
---

# 通用游戏登录流程（OCR + adb tap）

执行者目标：**让 `game.package_name` 进程启动**，不负责判定「已进入游戏内主场景」（观察者阶段负责）。

各游戏 UI 不同，但阶段类型高度相似。你必须 **先归类当前阶段**，再选动作；以 **当前 OCR** 为准，历史技能仅作参考。

## OCR 格式

`- (x, y) '文字' (置信度)` → 点击 **(x, y)**。操作后必须 `get_ocr_summary` 或 `tap_and_observe`，勿用轮次开局 OCR 快照。

## 阶段模型（按常见顺序，可跳过或循环）

| 阶段 ID | 典型 OCR / 画面 | 推荐动作 |
|---------|-----------------|----------|
| `splash` | 闪屏、Logo、健康游戏忠告、版号、加载中、% | `wait_seconds` 1～3s；无按钮则等待后重新 OCR |
| `system_permission` | 允许、拒绝、仅在使用时、悬浮窗、存储、电话 | 点 **允许/同意**；优先 `tap_and_observe` |
| `privacy` | 用户协议、隐私政策、我已阅读、同意并继续、勾选 | 先勾选（若有），再点 **同意/接受/进入** |
| `announcement` | 公告、活动、知道了、关闭、×、今日不再、跳过 | 点 **关闭/知道了/跳过**；全屏图可点右上角或底部主按钮 |
| `login` | 登录、注册、游客、一键登录、手机号、验证码、微信/QQ | 优先 **游客/一键登录**（若存在）；否则点主 **登录**；账号密码需 `credentials` 时由宿主提供（本工具无自动填表） |
| `server_select` | 选服、服务器、进入游戏、开始游戏、最近登录 | 点 **进入游戏/开始/确定**；多服列表点推荐服或第一项 |
| `download` | 下载资源、更新、解压、进度 % | `wait_seconds` 拉长；进度长时间不动可 `swipe` 或等待；仍无进程则继续观察 |
| `unknown` | 无法归类 | `get_ocr_summary`；疑似卡死则 `press_back` 或 `open_game_app` 重来 |

**顺序不固定**：可能先公告后隐私，或合并在一页；以 OCR 为准，不要机械按表从上到下。

## 标准操作循环

1. 开局控制器已 `open_game_app` / `am start`；先 `wait_seconds` 约 2s。
2. `get_ocr_summary` → 判断 **阶段 ID** → `tap_and_observe` 点最稳妥按钮。
3. 重复 2，直到你认为已触发游戏加载/登录完成。
4. 调用 **`wait_for_game_running(summary)`**（写明刚完成的阶段，如「已点进入游戏」）。
5. 成功返回后 **禁止再 tap**；失败则 `report_flow_done(success=false, summary=含阶段说明)`。

## 各阶段细则

### 隐私 / 协议

- 常见按钮：同意、接受、我知道了、进入游戏、勾选框+确认。
- 若同时有「拒绝」和「同意」，只点 **同意** 侧。
- 长协议页：优先点底部主按钮，勿盲点正文。

### 公告 / 活动

- 优先：关闭、×、跳过、知道了、领取后关闭。
- 轮播广告：点关闭或空白处无效时再试底部按钮。

### 登录

- 能 **游客 / 一键 / 快速开始** 则优先，减少输入。
- SDK 授权（微信/QQ）：若 OCR 出现第三方名，点对应图标后等待跳转。
- 卡在登录：检查是否需勾选协议；可 `wait_seconds` 后重 OCR。

### 选服

- 「进入游戏」「开始冒险」「确定」等等价。
- 列表页：点有「推荐」「新服」或列表第一项。

### 下载 / 更新

- 进度条阶段以等待为主；`executor.ad_initial_wait_s` 内可先 wait。
- 勿把「下载中」误判为失败；进程可能尚未出现。

## 工具约束

| 工具 | 用途 |
|------|------|
| `open_game_app` | 前台不是游戏包时优先调用，勿 monkey |
| `tap_coordinate` / `tap_and_observe` | 点击 OCR 坐标 |
| `swipe_screen` | 列表滚服、过长协议 |
| `press_back` | 误进子页；慎用，可能退出游戏 |
| `wait_seconds` | 加载/动画；**不能替代** `wait_for_game_running` |
| `wait_for_game_running` | 登录链尾声必调 |
| `read_login_flow_guide` | 重读本文件全文 |
| `list_learned_skills` / `read_learned_skill` | 可选，历史成功摘要 |

命令行（与 tap 工具等价）：`python -m game_agent.tools.adb_tap X Y [-s SERIAL]`

## 易错点

- 点错「拒绝」或退出 → 进程不起；用 `open_game_app` 重来。
- 未刷新 OCR 连续点同一坐标 → 界面已变仍盲点。
- 在进程已出现后继续 tap → 违反执行者/观察者边界。
- 把观察者才判的「创角/主场景」在执行者强求 → 执行者只需 **pidof 成功**。

## 汇报要求

每轮简要说明：**当前阶段 ID** + 下一步工具。`wait_for_game_running` 的 `summary` 须含最后点击的按钮含义（如「公告关闭后点进入游戏」）。
