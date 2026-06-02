---
name: gameturbo-log-baseline
description: >-
  判断 GameTurbo gameturbo.log 是否正常、如何区分可忽略噪声与真实故障。
  基于多段健康样本（changyou 系、魔灵幻想 gid=17044 等）。在分析 gameturbo.log、
  撰写 attempt_failure_report / failure_report、生成 GameTurbo 配置补丁、
  或用户询问加速日志是否正常时使用。
---

# GameTurbo 日志正常基线（AI 判定指引）

## 何时须阅读本文件

- 分析 `gameturbo.log` 或 `domain_region_analysis.json` 判断加速是否工作正常；
- 编写 `attempt_failure_report.md` / `failure_report.md` 或 `GameTurboConfigPatch`；
- 用户提供了失败日志，需要区分「真故障」与「正常重连/缓冲」；
- 怀疑 `tunnel closed`、`heartbeat timeout` 等关键字是否代表失败。

---

## 健康会话应具备的生命周期

按时间顺序，**至少**应出现以下阶段（允许交错、重复）。

> 日志可能从会话中途开始抓取（只见 `E2E RTT` + `BHOOK`，不见初始化头）。**只要 2–6 步完整，仍可判健康**；勿因缺少下文「0. 启动块」 alone 判失败。

**0. 启动块（可选，完整 logcat 常见）**

- `Load …/libgameturbo.so … ok`、`JNI_OnLoad` / `nativeInit`
- `=== GameTurbo initializing ===` → `config loaded: game=<gid> … patterns=N`
- `probing N entry points…` → `selected entry: <ip>:9443 (rtt=… ms)`
- `shadowhook_init failed: … (non-fatal, will try bytehook anyway)` — **可忽略**
- `hooks installed` → `crypto:` → `AUTH sent` → `tunnel initialized:` / `tunnel authenticated (AUTH_OK received)`
- `=== GameTurbo initialized ===`

**1–6. 数据面（必需）**

1. **探针/注入就绪**：`E2E RTT: Nms`（常见 20–150ms，**整段出现十几次即可**，不必上百次）；大量 `[BHOOK] OK:`。游戏主引擎 so 因游戏而异（如 `libtuanjie.so`、`libil2cpp.so`、`libxlua.so`），且常有 `libgameturbo.so`。
2. **连接创建**：`[SOCKET] fd=… domain=… type=…`。
3. **SNI 待解析**：`[PENDING-SNI] <ip>:443` 或 `[::ffff:x.x.x.x]:443`。
4. **路由决策**（每条 PENDING 后应出现其一）：
   - `[SNI-DIRECT] <域名> (fd N)` — 走直连；
   - `[SNI-TUNNEL] <域名> (fd N)` — 走隧道。
5. **隧道流建立**（仅 TUNNEL 域名）：
   - `stream <id> opened (proto=1)`
   - `[TUNNEL] prealloc notify owner_fd=… efd=… stream=…`
   - `[SEND-TUNNEL] fd=… stream=… len=…`（常见首包序列 `len=42`、`len=6`、`len=45` 后接较大 payload，类似 TLS 握手）
   - `[GETSOCKNAME] tunneled fd=…, returning fake local addr`
6. **持续数据面**：重复 `[SEND-TUNNEL]`、`[FEC] recovered shard …`、周期性 `E2E RTT`。
7. **可选恢复**（部分对局**全程无** heartbeat 重连；部分对局中途出现一次。均可能健康）：
   - `heartbeat timeout … reconnecting…`
   - `rebuilding tunnel (attempt N)…`
   - `crypto: AES-128-GCM enabled` → `AUTH sent` → `tunnel rebuilt: conv=…` → `tunnel authenticated (AUTH_OK received)`

**结论**：仅有上述「重连/缓冲」关键字、但数据面在恢复后继续收发，**不能**单独判为加速失败。

---

## 日志标签速查

| 标签/行 | 含义 | 正常时 |
|--------|------|--------|
| `E2E RTT` | 端到端延迟采样 | 周期性出现，ms 级合理值 |
| `[BHOOK] OK:` | libc 钩子安装成功 | 大量出现，无 `BHOOK` 失败行 |
| `[SOCKET]` | 创建 socket | 与 `[PENDING-SNI]` 成对增多 |
| `[PENDING-SNI]` | 尚未知域名的 TLS 连接 | 后应紧跟 `SNI-DIRECT` 或 `SNI-TUNNEL` |
| `[SNI-DIRECT]` | 配置为直连 | SDK/CDN/统计域名常见 |
| `[SNI-TUNNEL]` | 配置为隧道 | 游戏服/关键业务域名 |
| `stream N opened` | 隧道内流 | 紧跟 `SNI-TUNNEL` |
| `[TUNNEL] prealloc notify` | 隧道预分配通知 | 与 stream 对应 |
| `[SEND-TUNNEL]` | 经隧道发送 | 游戏进行中应持续出现 |
| `[GETSOCKNAME] tunneled` | 对隧道 fd 伪装本地地址 | 与 SEND-TUNNEL 同阶段 |
| `[FEC] recovered shard` | 前向纠错恢复 | 基准中极多，**属正常** |
| `stream N recv buffer full, dropping` | 接收缓冲满丢包 | 基准中 **2000+ 次仍属正常**；看恢复后是否仍有 SEND-TUNNEL |
| `[CHROMIUM-SOCKOPT]` / `[CHROMIUM-EPOLL]` | WebView 侧探测 | `errno=88`、`UNKNOWN(39)` 常见，**忽略** |
| `heartbeat timeout` + `rebuilding tunnel` | 控制面心跳超时重连 | 若随后 `AUTH_OK` 且数据面恢复 → 正常 |
| `[TUNNEL-TCP] stream open failed, falling back to direct` | 隧道内 TCP 失败改直连 | 重连窗口内可出现；看业务是否仍通 |
| `crypto:` / `AUTH sent` / `tunnel authenticated` | 隧道鉴权 | 启动或重连后出现 `AUTH_OK` |
| `config loaded:` / `selected entry:` | 配置与入口探测 | 启动块；说明 gid/规则已加载 |
| `shadowhook_init failed` (non-fatal) | 备用 hook 方案 | **忽略** |
| `tunnel initialized:` | 控制通道就绪 | 启动后常见 |

---

## 基准样本中的数量级（勿误杀）

两段健康样本（不同游戏）统计，**数量仅作量级参考**，勿作硬阈值：

| 现象 | 样本 A（changyou 系） | 样本 B（gid=17044 魔灵幻想） | AI 判定 |
|------|----------------------|------------------------------|---------|
| 行数 | ~4480 | ~3046 | — |
| `recv buffer full, dropping` | 2200+ | ~1000 | 单独 **≠ 失败** |
| `[FEC] recovered shard` | 1000+ | ~380 | 正常 |
| `[SEND-TUNNEL]` | 100+ | ~400 | 隧道在用 |
| `[SNI-TUNNEL]` / `[SNI-DIRECT]` | 数十 | ~17 / ~23 | 路由在工作 |
| `E2E RTT` | ~86 次 | ~19 次 | **次数少仍可正常** |
| `heartbeat timeout` → `tunnel rebuilt` | 1 次 | **0 次** | 无重连也可健康 |
| `tunnel closed` | 0 | 0 | 两样本均未出现 |
| 启动块 `GameTurbo initializing` | 可能未包含 | 有（~800 行后进入数据面） | 抓取起点不同 |

---

## 常见「看起来像错、实则正常」

1. **`[PENDING-SNI]` 只有 IP、暂时没有域名**  
   随后出现 `SNI-DIRECT`/`SNI-TUNNEL` 即正常；长时间只有 PENDING 无 SNI 行才可疑。

2. **同一域名多个 fd、重复 `SNI-TUNNEL`**  
   多连接并发，正常。

3. **`recv buffer full, dropping … bytes`**  
   高流量时接收端背压；若之后仍有 `[SEND-TUNNEL]`、`[FEC]`，游戏仍可正常。

4. **`heartbeat timeout (18446744073709551615 ms …)`**  
   异常大的 ms 多为计时器未初始化/溢出展示；关键看是否 `rebuilding` → `tunnel authenticated` → 恢复发送。

5. **`[TUNNEL-TCP] stream open failed, falling back to direct connect`**  
   重连或单流失败时的降级；若仅偶发且业务域名仍有 TUNNEL 流，不必判整体失败。

6. **大量 `[BHOOK] OK` 来自 WebView / 系统 so**  
   只要游戏主引擎 so（`libtuanjie.so` / `libil2cpp.so` / `libxlua.so` 等）及 `libgameturbo.so` 也有 OK 即可。

7. **直连与隧道并存**  
   统计/渠道/SDK 走 `SNI-DIRECT`，游戏服/CDN 走 `SNI-TUNNEL` 是预期行为，**不是**配置全错。

8. **`[PENDING-SNI]` 略多于 `[SNI-DIRECT|TUNNEL]`**  
   并发连接时部分 pending 在片段末尾尚未解析；仅当**长时间大量** pending 且无对应 SNI 才可疑。

9. **`shadowhook_init failed` + `bytehook`**  
   启动日志写明 non-fatal，后续仍有 `hooks installed` 即正常。

10. **整段无 `heartbeat timeout` / `tunnel rebuilt`**  
    样本 B 仅启动时一次 `AUTH_OK`，会话内无中途重连，仍全程 `SEND-TUNNEL` + FEC，属健康。

---

## 高置信度异常信号（应写入报告）

| 信号 | 说明 |
|------|------|
| `tunnel closed` 且之后**无** `tunnel rebuilt` / `tunnel authenticated` / 新 `SEND-TUNNEL` | 隧道可能真断 |
| 游戏关键域名（配置为 tunnel）仅有 `PENDING-SNI` 或长期无 `SNI-TUNNEL` | 规则未命中或域名错误 |
| 整段日志**无** `E2E RTT` 且**无** `[BHOOK] OK` | 加速未加载或 logcat 抓错 tag |
| 无 `[SEND-TUNNEL]` / `stream opened`，但观察者已进游戏 | 隧道数据面未建立 |
| `rebuilding tunnel (attempt N)` 中 N 持续增大且无 `AUTH_OK` | 重连失败 |
| `domain_region_analysis.json` 中关键域名为 `unknown` 或 `non_china` 与预期不符 | 结合 geo 判断配置 |
| 应有 TUNNEL 的域名在整段日志中**仅** `SNI-DIRECT` | 可能误配 direct_patterns |

---

## AI 分析工作流（必须遵守）

1. **先对照基线**：用本节「生命周期」扫一遍，确认是否具备 1–6 步。
2. **再查 domain JSON**：`tunnel_domains` / `direct_domains` / `unknown` / `unmatched_pending_ips` 与日志中的 `SNI-*` 是否一致。
3. **区分控制面与数据面**：
   - 控制面：heartbeat、rebuild、AUTH；
   - 数据面：SEND-TUNNEL、FEC、stream opened。
   控制面抖动但数据面持续 → 降级为 warning，勿判死刑。
4. **写报告时**：
   - 引用 **具体行片段**（含标签与域名）；
   - 标明「符合基线 / 偏离基线」；
   - 配置建议只针对**偏离**项，勿因 `recv buffer full` 或单次 `heartbeat timeout` 就改 JSON。
5. **与画面结论交叉验证**：日志正常但黑屏 → 优先查游戏进程/登录画面/执行者阶段，勿强行归因网络。

---

## 配置补丁方向提示（Modify 阶段，自动化仅允许 direct_patterns / port_rules）

本项目测的是 **tunnel 加速**。`default_action` 保持 **tunnel**；自动化**不会**改 `tunnel_patterns`。

| 日志/JSON 现象 | 补丁方向 |
|----------------|----------|
| `direct_domains` 中为渠道/SDK/统计/CDN 资源 | 可**谨慎**追加到 `direct_patterns`（须与 domain_region_analysis.json 一致） |
| `unknown_domains` / 游戏区服/登录/网关 | **不要**为「能连上」而 bulk direct；优先查节点/规则，多数应继续 tunnel |
| 长期 `unmatched_pending_ips` | 先结合 JSON 与日志反查；仅当确认为资源 CDN 才 direct |
| 仅隧道域异常、直连正常 | 查 AUTH/rebuild/节点，非加 direct |
| 全直连、无 TUNNEL | 检查配置是否未加载或过度 direct，**不要**再扩大 direct_patterns |

域名分析：**不读 shell 终端输出**；须读 artifact 内 `domain_region_analysis.json`。
由 `game_agent.utils.gameturbo_log_domain_extract` 生成（逻辑对齐 `extract_domain_region_from_log.sh` + `check_target_stability.py`，不改原文件）。
Modify 无此 JSON 时禁止出配置补丁。

---

## 基准样本摘录（启动 + 鉴权，样本 B）

```
=== GameTurbo initializing ===
config loaded: game=17044 ver=1 entry=42.240.157.172:9443 mode=normal … patterns=72
probing 5 entry points...
selected entry: 103.49.62.41:9443 (rtt=8 ms)
shadowhook_init failed: 12 (non-fatal, will try bytehook anyway)
hooks installed (bytehook automatic mode)
crypto: AES-128-GCM enabled
AUTH sent (195 bytes, device=…)
tunnel initialized: 103.49.62.41:9443 conv=…
=== GameTurbo initialized ===
tunnel authenticated (AUTH_OK received)
```

## 基准样本摘录（健康隧道握手，样本 A / B 通用）

```
[SNI-TUNNEL] zt-serverlist-qipa-prod.xxchangyou.com (fd 350)
stream 3 opened (proto=1)
[TUNNEL] prealloc notify owner_fd=350 efd=188 stream=3
[SEND-TUNNEL] fd=350 stream=3 len=42
[SEND-TUNNEL] fd=350 stream=3 len=6
[SEND-TUNNEL] fd=350 stream=3 len=45
[GETSOCKNAME] tunneled fd=350, returning fake local addr
[SEND-TUNNEL] fd=350 stream=3 len=298
```

```
[SNI-TUNNEL] elf-cdn1.yezixigame.com (fd 174)
stream … opened (proto=1)
[TUNNEL] prealloc notify …
[SEND-TUNNEL] fd=… stream=… len=42
```

## 基准样本摘录（健康重连，样本 A；样本 B 可不出现）

```
heartbeat timeout (18446744073709551615 ms since last PONG), reconnecting...
rebuilding tunnel (attempt 1)...
AUTH sent (190 bytes, device=...)
tunnel rebuilt: conv=...
[TUNNEL-TCP] stream open failed, falling back to direct connect
tunnel authenticated (AUTH_OK received)
[SNI-TUNNEL] zt-cdn-qipa-prod.xxchangyou.com (fd 183)
stream 1 opened (proto=1)
[SEND-TUNNEL] fd=183 stream=1 len=42
```

---

## 禁止项（避免误报）

- 勿将 **`tunnel closed` 未出现** 的日志判为「缺少 closed 所以异常」。
- 勿将 **`recv buffer full` 次数多** 单独作为失败根因。
- 勿将 **`heartbeat timeout` 单次** 等同于「玩家无法联网」，须看恢复链。
- 勿建议把 **已全部 DIRECT 的统计 SDK 域名** 强行改为 TUNNEL。
- 勿在证据不足时写「配置完全错误」；应列 `evidence_gaps`。
