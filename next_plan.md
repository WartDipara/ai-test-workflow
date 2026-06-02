# 整体项目流程图

```mermaid
graph TD
    start[开始] --> preprocess[预处理<br/>APK 下载 + ABI 剥离]
    preprocess --> init[Init<br/>GameTurbo bootstrap + deploy]
    init --> executor[执行者<br/>OCR + AI + adb tap 登录启动游戏]

    executor --> observer{观察者阶段}
    observer --> log_mon[LogMonitor<br/>GameTurbo 日志]
    observer --> screen_mon[ScreenMonitor<br/>截图 + 多模态]
    observer --> game_entry[GameEntryDetector<br/>进入游戏判定]
    observer --> session[SessionCoordinator<br/>进程 crash/重启]

    game_entry -->|确认进入| normal_exit[正常退出<br/>force-stop 游戏]
    observer -->|异常| failure[失败收尾]
    executor -->|失败| failure

    failure --> cleanup[导出日志 / 域名分析 / 卸载]
    cleanup --> retry{重试?}
    retry -->|是| modify[AI 改配置 + deploy]
    modify --> executor
    retry -->|否| final_fail[最终失败]

    normal_exit --> success[成功交付<br/>.gameturbo_merged.json]
```

## 当前架构说明

**执行者阶段**：AI 通过 PaddleOCR 获取画面文字与坐标，调用 `tap_coordinate` / `tap_and_observe`（底层 `adb input tap`）完成登录与启动；检测到 `game.package_name` 进程后进入观察者。

**观察者阶段**：并行监控 GameTurbo 日志、画面异常、是否进入游戏内、游戏进程是否 crash/重启。

**失败与重试**：导出日志与截图 → force-stop 游戏 → 卸载 → AI 分析并修改 `games/*.json` → `deploy.sh` → 从执行者阶段重新跑完整流程。

详见 [README.md](README.md) 与 [skills/game-launch-ocr/SKILL.md](skills/game-launch-ocr/SKILL.md)。
