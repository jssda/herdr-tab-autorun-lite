#!/bin/sh
# herdr 插件动作：打开选择器弹窗
set -eu
exec "${HERDR_BIN_PATH:-herdr}" plugin pane open --plugin jssd.tab-autorun --entrypoint new-tab-picker
