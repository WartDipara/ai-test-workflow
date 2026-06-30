#!/bin/bash
export MSYS_NO_PATHCONV=1

TIMESTAMP=$(date +%Y%m%d_%H%M%S)

COUNT=10
INTERVAL=0.2   # 100ms

echo "Capturing ${COUNT} screenshots..."

# 连续截图到手机
for i in $(seq 1 $COUNT); do
    REMOTE_PATH="/sdcard/sc_tmp_${i}.png"

    adb shell screencap -p "$REMOTE_PATH"

    # 最后一张不用sleep
    if [ "$i" -lt "$COUNT" ]; then
        sleep "$INTERVAL"
    fi
done

echo "Pulling screenshots..."

# 拉回电脑
for i in $(seq 1 $COUNT); do
    REMOTE_PATH="/sdcard/sc_tmp_${i}.png"
    LOCAL_NAME="screenshot_${i}.png"

    adb pull "$REMOTE_PATH" "./${LOCAL_NAME}"
    adb shell rm "$REMOTE_PATH"

    if [ -f "./${LOCAL_NAME}" ]; then
        echo "Saved ${LOCAL_NAME}"
    else
        echo "Failed ${LOCAL_NAME}"
    fi
done

echo "Done."