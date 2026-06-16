---
name: gameturbo_log_baseline
skill_id: gameturbo_log_baseline
description: >-
  GameTurbo log health baseline. Index: skills/SKILL.md → read_repo_skill("gameturbo_log_baseline").
---

# GameTurbo log healthy baseline (AI judgment guide)

## When to read this file

- **Runtime:** the executor does **not** rule-judge `gameturbo.log` for fail-fast; logs are collected only. Network-like failures use OCR + multimodal during the run; **Modify** stage combines archived log + `domain_region_analysis.json`.
- Analyzing `gameturbo.log` or `domain_region_analysis.json` for acceleration health;
- Writing `attempt_failure_report.md` / `failure_report.md` or `GameTurboConfigPatch`;
- User supplied failure logs — distinguish real faults vs normal reconnect/buffering;
- Unsure if `tunnel closed`, `heartbeat timeout`, etc. mean failure.

---

## Healthy session lifecycle

Chronologically, **at least** these phases should appear (may overlap/repeat).

> Capture may start mid-session (only `E2E RTT` + `BHOOK`, no init header). **Steps 2–6 complete ⇒ still healthy**; missing section "0. startup" alone is not failure.

**0. Startup block (optional, full logcat)**

- `Load …/libgameturbo.so … ok`, `JNI_OnLoad` / `nativeInit`
- `=== GameTurbo initializing ===` → `config loaded: game=<gid> … patterns=N`
- `probing N entry points…` → `selected entry: <ip>:9443 (rtt=… ms)`
- `shadowhook_init failed: … (non-fatal, will try bytehook anyway)` — **ignore**
- `hooks installed` → `crypto:` → `AUTH sent` → `tunnel initialized:` / `tunnel authenticated (AUTH_OK received)`
- `=== GameTurbo initialized ===`

**1–6. Data plane (required)**

1. **Probe/hooks ready**: `E2E RTT: Nms` (often 20–150ms; **~10+ times in segment is enough**); many `[BHOOK] OK:` (game engine so varies: `libtuanjie.so`, `libil2cpp.so`, `libxlua.so`, plus `libgameturbo.so`).
2. **Sockets**: `[SOCKET] fd=… domain=… type=…`.
3. **Pending SNI**: `[PENDING-SNI] <ip>:443` or `[::ffff:x.x.x.x]:443`.
4. **Routing** (after each PENDING, one of):
   - `[SNI-DIRECT] <domain> (fd N)` — direct;
   - `[SNI-TUNNEL] <domain> (fd N)` — tunnel.
5. **Tunnel streams** (TUNNEL domains only):
   - `stream <id> opened (proto=1)`
   - `[TUNNEL] prealloc notify owner_fd=… efd=… stream=…`
   - `[SEND-TUNNEL] fd=… stream=… len=…` (often `len=42`, `len=6`, `len=45` then larger payloads)
   - `[GETSOCKNAME] tunneled fd=…, returning fake local addr`
6. **Sustained data plane**: repeat `[SEND-TUNNEL]`, `[FEC] recovered shard …`, periodic `E2E RTT`.
7. **Optional recovery** (some sessions never heartbeat-reconnect; some once — both can be healthy):
   - `heartbeat timeout … reconnecting…`
   - `rebuilding tunnel (attempt N)…`
   - `crypto: AES-128-GCM enabled` → `AUTH sent` → `tunnel rebuilt: conv=…` → `tunnel authenticated (AUTH_OK received)`

**Conclusion**: control-plane reconnect/buffer keywords alone, with data plane resuming after recovery, **do not** mean acceleration failed.

---

## Log tag quick reference

| Tag/line | Meaning | When healthy |
|--------|------|--------|
| `E2E RTT` | E2E latency sample | Periodic, reasonable ms |
| `[BHOOK] OK:` | libc hook OK | Many; no BHOOK failures |
| `[SOCKET]` | socket created | Grows with PENDING-SNI |
| `[PENDING-SNI]` | TLS before domain known | Followed by SNI-DIRECT or SNI-TUNNEL |
| `[SNI-DIRECT]` | direct route | SDK/CDN/stats common |
| `[SNI-TUNNEL]` | tunnel route | game server / key business; may show trailing `sni geo default` triple |
| `[IPV6-RULE]` | IPv6 default route | often direct without SNI — may be invisible in domain JSON |
| `stream N opened` | tunnel stream | After SNI-TUNNEL |
| `[TUNNEL] prealloc notify` | tunnel prealloc | Matches stream |
| `[SEND-TUNNEL]` | send via tunnel | Continuous in play |
| `[GETSOCKNAME] tunneled` | fake local addr on tunnel fd | With SEND-TUNNEL |
| `[FEC] recovered shard` | FEC recovery | Very frequent in baseline — **normal** |
| `stream N recv buffer full, dropping` | RX backpressure | **2000+ still normal** in baseline if SEND-TUNNEL continues |
| `[CHROMIUM-SOCKOPT]` / `[CHROMIUM-EPOLL]` | WebView probes | `errno=88`, `UNKNOWN(39)` — **ignore** |
| `heartbeat timeout` + `rebuilding tunnel` | control reconnect | After `AUTH_OK` + data plane → normal |
| `[TUNNEL-TCP] stream open failed, falling back to direct` | TCP fallback | OK in reconnect window if business still works |
| `crypto:` / `AUTH sent` / `tunnel authenticated` | tunnel auth | `AUTH_OK` on start or reconnect |
| `config loaded:` / `selected entry:` | config / entry probe | startup; gid/rules loaded |
| `shadowhook_init failed` (non-fatal) | alternate hook | **ignore** |
| `tunnel initialized:` | control ready | after startup |

---

## Baseline magnitude (do not over-penalize)

Two healthy samples (different games) — **order of magnitude only**, not hard thresholds:

| Phenomenon | Sample A (changyou) | Sample B (gid=17044) | AI rule |
|------|----------------------|------------------------------|---------|
| Lines | ~4480 | ~3046 | — |
| `recv buffer full, dropping` | 2200+ | ~1000 | alone **≠ failure** |
| `[FEC] recovered shard` | 1000+ | ~380 | normal |
| `[SEND-TUNNEL]` | 100+ | ~400 | tunnel in use |
| `[SNI-TUNNEL]` / `[SNI-DIRECT]` | tens | ~17 / ~23 | routing works |
| `E2E RTT` | ~86 | ~19 | **few RTT lines still OK** |
| `heartbeat timeout` → `tunnel rebuilt` | 1 | **0** | no mid reconnect still OK |
| `tunnel closed` | 0 | 0 | absent in both samples |
| `GameTurbo initializing` block | may missing | present | capture start differs |

---

## Looks bad but often normal

1. **`[PENDING-SNI]` IP only, no domain yet** — OK if followed by `SNI-DIRECT`/`SNI-TUNNEL`; suspicious if PENDING persists with no SNI lines.
2. **Same domain, multiple fds, repeat `SNI-TUNNEL`** — concurrent connections, normal.
3. **`recv buffer full, dropping … bytes`** — backpressure; OK if `[SEND-TUNNEL]` / `[FEC]` continue.
4. **`heartbeat timeout (18446744073709551615 ms …)`** — huge ms often timer display artifact; check rebuild → `tunnel authenticated` → sends resume.
5. **`[TUNNEL-TCP] stream open failed, falling back to direct connect`** — occasional fallback; OK if business domains still get TUNNEL streams.
6. **Many `[BHOOK] OK` from WebView / system so** — need game engine + `libgameturbo.so` OK too.
7. **Direct and tunnel coexist** — stats/channel/SDK DIRECT + game/CDN TUNNEL is expected, **not** "all config wrong".
8. **PENDING slightly more than SNI lines** — tail of capture; suspicious only if **long** pending without SNI.
9. **`shadowhook_init failed` + bytehook** — non-fatal; OK if `hooks installed` follows.
10. **No `heartbeat timeout` / `tunnel rebuilt` entire session** — sample B: one `AUTH_OK` at start, full session SEND-TUNNEL + FEC — healthy.

---

## High-confidence fault signals (put in reports)

| Signal | Meaning |
|------|------|
| `tunnel closed` with **no** later `tunnel rebuilt` / `tunnel authenticated` / new `SEND-TUNNEL` | tunnel may be dead |
| Key tunnel-config domains: only `PENDING-SNI` or no `SNI-TUNNEL` for long time | rules miss or wrong domain |
| **No** `E2E RTT` and **no** `[BHOOK] OK` whole segment | accel not loaded or wrong logcat tag |
| No `[SEND-TUNNEL]` / `stream opened` but observer says in-game | data plane not up |
| `rebuilding tunnel (attempt N)` with N growing, no `AUTH_OK` | reconnect failing |
| `domain_region_analysis.json`: key domains `unknown` / `non_china` vs expectation | geo + config review |
| Domain should be TUNNEL but log shows **only** `SNI-DIRECT` | possible mistaken direct_patterns |

---

## AI analysis workflow (mandatory)

1. **Baseline first**: scan lifecycle steps 1–6.
2. **Domain JSON**: `tunnel_domains` / `direct_domains` / `unknown` / `unmatched_pending_ips` vs log `SNI-*`.
3. **Control vs data plane**:
   - control: heartbeat, rebuild, AUTH;
   - data: SEND-TUNNEL, FEC, stream opened.
   Control jitter + sustained data plane → warning, not fatal.
4. **Reports**:
   - cite **concrete log lines** (tags + domains);
   - mark baseline match / deviation;
   - config changes only for deviations; do not patch JSON for `recv buffer full` or single `heartbeat timeout`.
5. **Cross-check UI**: healthy log + black screen → game process / login / executor first, not forced network blame.

---

## SNI-TUNNEL / SNI-DIRECT trailing triple (sniRoute geoRoute defaultRoute)

Some lines print three integers after the fd, e.g.:

`[SNI-TUNNEL] ylx10cdn.youjiayouxi.cn → 220.194.72.86:80 (fd 128) -1 -1 1`

Source: `GameTurbo-Native/client/android/src/gt_intercept.c` — `sniRoute`, `geoRoute`, `defaultRoute`.

| Position | Variable | Meaning | `-1` means |
|----------|----------|---------|------------|
| 1st | `sniRoute` | `direct_patterns` / `tunnel_patterns` / `domain_rules` | **No domain rule matched** (not failure) |
| 2nd | `geoRoute` | GeoIP (`directForeignIP` / `tunnelChineseIP`) | **Geo did not decide** (not "domain abnormal") |
| 3rd | `defaultRoute` | `default_action` mapped (tunnel=1, direct=0) | Current default (e.g. `tunnel` → `1`) |

Tunnel is chosen when: `sniRoute==1`, or (`sniRoute==-1` and geo says tunnel), or (`sniRoute==-1` and `geoRoute==-1` and `defaultRoute==tunnel`).

**Common patterns**

- `-1 -1 1` + `[SNI-TUNNEL]` + `[SEND-TUNNEL]` → data plane healthy; routing is **default tunnel only** (no per-domain rule).
- `-1 -1 1` does **not** mean acceleration failed; the tag is already `[SNI-TUNNEL]`.
- `-1 -1 1` also does **not** prove this domain **should** stay on tunnel — only that default said tunnel.
- `1 * *` or `0 * *` → domain rule explicitly decided; trust 1st number.
- `[SNI-DIRECT]` lines: read 1st/2nd numbers; do not infer from 3rd alone.

**Misread to avoid**: `-1` ≠ "GameTurbo ineffective" ≠ automatic reason to add `direct_patterns`.

---

## Config suggestion decision tree (Modify / failure report)

Use with `domain_region_analysis.json` and executor failure code (E2006/E2002/etc.).

| Observation | Config action |
|-------------|----------------|
| `-1 -1 1` + TUNNEL + SEND-TUNNEL + **game flow OK** | **No change** |
| `-1 -1 1` + TUNNEL + SEND-TUNNEL + **E2006/E2002** + domain looks like **CDN/resource** (`cdn`, `static`, `res`, `pkg` in name) | **Trial** one `direct_patterns` entry for that CDN domain on next retry; verify server list / download / UI |
| `direct_domains` with clear CDN/channel/SDK | Cautiously append matching `direct_patterns` |
| `tunnel_domains` that are login/gateway/realm/sync core | **Do not** direct; check executor / PCAP for API domain |
| `[IPV6-RULE] … → direct` with **no SNI** domain in JSON | Log `evidence_gaps`; PCAP for API hostname; **never** add bare IP to `direct_patterns` |
| `unknown_domains` / bulk "just connect" | **No** bulk direct |
| Trial direct on CDN still fails E2006 | Roll back direct; investigate no-SNI API (IPV6-RULE) or executor |

**16914 example (gid 16914, server list E2006)**

- Log: `ylx10cdn.youjiayouxi.cn … -1 -1 1` + heavy SEND-TUNNEL — tunnel works; game server slot still empty.
- **Hypothesis A (config trial)**: CDN named domain tunneled only by default → next Modify try `direct_patterns: ["ylx10cdn.youjiayouxi.cn"]` (single variable; keep `ylx10pkg` unchanged initially).
- **Hypothesis B (parallel)**: `[IPV6-RULE] ::ffff:139.177.246.x → direct` near failure time — possible server-list API without SNI; needs PCAP; separate from CDN direct trial.

---

## Modify-stage patch hints (automation: direct_patterns / port_rules only)

This project tests **tunnel acceleration**. Keep `default_action` **tunnel**; automation does **not** change `tunnel_patterns`.

| Log/JSON | Patch direction |
|----------------|----------|
| channel/SDK/stats/CDN in `direct_domains` | **cautiously** append `direct_patterns` (must match JSON) |
| CDN-like name in `tunnel_domains` + E2006/E2002 + log `-1 -1 1` on that domain | **one-domain direct trial** (see decision tree above) |
| `unknown_domains` / login/gateway/server | **no** bulk direct to "just connect"; prefer tunnel |
| long `unmatched_pending_ips` | investigate; direct only if confirmed resource CDN |
| tunnel domains bad, direct OK | check AUTH/rebuild/entry, not more direct |
| all direct, no TUNNEL | config not loaded or over-direct; **do not** widen direct_patterns |
| `[IPV6-RULE]` direct traffic, empty `unknown_domains` | PCAP / evidence_gaps; not IP-only direct_patterns |

Domain analysis: read artifact `domain_region_analysis.json` (not shell stdout).
Generated by `game_agent.utils.gameturbo_log_domain_extract` (aligned with `extract_domain_region_from_log.sh` + `check_target_stability.py`).
No JSON ⇒ no automated config patch in Modify.

---

## Sample excerpts (startup + auth, sample B)

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

## Healthy tunnel handshake (samples A/B)

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

## Healthy reconnect (sample A; sample B may omit)

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

## Do not (false positives)

- Do not fail logs **because** `tunnel closed` never appeared.
- Do not use **`recv buffer full` count alone** as root cause.
- Do not equate **single** `heartbeat timeout` with "player offline" without recovery chain.
- Do not force stats SDK domains that are all DIRECT to TUNNEL.
- Do not claim "config totally wrong" without evidence; list `evidence_gaps`.
