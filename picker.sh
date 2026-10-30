#!/bin/sh
# 选择器：在 popup pane 里选这个 tab 要跑什么。
# 有 fzf 就用 fzf（可搜索），没有就用插件自带的 python 菜单 —— fzf 不是硬依赖。
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=python3

if [ "$("$PY" "$ROOT/autorun.py" --picker-mode)" != "fzf" ]; then
  exec "$PY" "$ROOT/autorun.py" --pick-exec
fi

choices=$("$PY" "$ROOT/autorun.py" --choices)
[ -n "$choices" ] || exit 0

pick=$(printf '%s\n' "$choices" | fzf \
  --prompt='新 tab 执行 > ' \
  --height=100% \
  --reverse \
  --no-info \
  --no-multi \
  --with-nth=1 \
  --delimiter='\t' \
  --header='回车 = 新建 tab 并执行 · Esc = 取消' || true)

[ -n "${pick:-}" ] || exit 0

label=$(printf '%s' "$pick" | cut -f1)
exec "$PY" "$ROOT/autorun.py" --new "$label"
