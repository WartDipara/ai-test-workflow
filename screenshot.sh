#!/bin/bash
export MSYS_NO_PATHCONV=1

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="screenshot_${TIMESTAMP}.png"
REMOTE_PATH="//sdcard/sc_tmp.png"

echo "capturing screenshot..."
adb shell screencap -p $REMOTE_PATH && \
adb pull $REMOTE_PATH "./${FILENAME}" && \
adb shell rm $REMOTE_PATH

if [ -f "./${FILENAME}" ]; then
    echo "OK!"
else
    echo "ERROR!"
fi