#!/usr/bin/env python3
"""herdr 插件：新 tab 里自动跑一条命令或起一个 agent。

触发方式：
  python3 autorun.py --event     tab.created 事件钩子（manifest 里声明）
  python3 autorun.py --manual    在当前 tab 里手动补跑一次
  python3 autorun.py --doctor    打印当前 tab 匹配到的规则，不执行
  python3 autorun.py --toggle    打开/关闭自动运行

配置：$HERDR_PLUGIN_CONFIG_DIR/config.toml（首次运行自动生成）
状态：$HERDR_PLUGIN_STATE_DIR/disabled（toggle 开关）、seen.txt（去重）
"""

import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # 3.9/3.10 可以装 tomli
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
CONFIG_DIR = Path(
    os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    or Path.home() / ".config/herdr/plugins/config/jssd.tab-autorun"
)
STATE_DIR = Path(
    os.environ.get("HERDR_PLUGIN_STATE_DIR")
    or Path.home() / ".local/state/herdr/plugins/jssd.tab-autorun"
)
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG = '''# herdr tab-autorun-lite — edits apply immediately, no restart needed
#                       改完立即生效，无需重启 herdr

enabled = true

# Brand-new workspace's first tab: skip (recommended — you often cd around first) | run
# 新建 workspace 时那个初始 tab：skip（默认，通常要先 cd）/ run（照常自动执行）
new_workspace = "skip"

# Picker menu: auto (fzf if present) | builtin | fzf
# 选择器菜单：auto（有 fzf 就用）/ builtin（永远内置）/ fzf（只认 fzf）
picker = "auto"

# ---------- What to run in a new tab / 新 tab 里做什么 ----------
[default]
# agent = start an agent · command = run a shell command · skip = do nothing
# agent = 起 agent · command = 跑命令 · skip = 什么都不做
mode = "agent"
# hermes / opencode / claude / codex ... (mode = "agent")
kind = "hermes"
# command line used when mode = "command" / mode = "command" 时执行的命令
command = ""
# extra agent args, e.g. ["--model", "deepseek-flash"] / 传给 agent 的额外参数
agent_args = []
# max wait for the new pane's shell prompt / 等提示符就绪的上限（毫秒）
ready_timeout_ms = 8000

# ---------- Per-directory overrides / 按目录或仓库覆盖 ----------
# First matching rule wins; every predicate in a rule must match.
# 从上到下匹配，第一条命中的生效；一条规则里所有条件都要满足。
#
# [[rules]]
# name = "backend repo uses opencode"
# cwd_glob = "~/code/backend/**"
# repo_name = "backend"
# mode = "agent"
# kind = "opencode"
#
# [[rules]]
# name = "keep scratch a plain shell / 这个目录保持裸 shell"
# cwd_glob = "~/code/scratch/**"
# mode = "skip"

# ---------- Picker entries / 选择器选项（prefix+t） ----------
# Omit this section to use the built-in hermes / opencode / shell.
# 不写这一段则用内置的 hermes / opencode / shell。
#
# [[choices]]
# label = "hermes"
# mode = "agent"
# kind = "hermes"
#
# [[choices]]
# label = "dev server"
# mode = "command"
# command = "npm run dev"
'''

DEFAULT_CHOICES = [
    {"label": "hermes", "mode": "agent", "kind": "hermes"},
    {"label": "opencode", "mode": "agent", "kind": "opencode"},
    {"label": "shell", "mode": "skip"},
]

READY_POLL_MS = 60
BUSY_MARKERS = ("agent_pane_busy", "agent_launch_pending", "not_ready", "busy")


def log(msg: str) -> None:
    print(f"[tab-autorun] {msg}", flush=True)


def herdr(*args: str) -> dict:
    """调用 herdr CLI，返回 result 字段；失败抛异常。"""
    proc = subprocess.run(
        [HERDR, *args], capture_output=True, text=True, timeout=120
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"herdr {' '.join(args)} 失败(exit={proc.returncode}): {out or proc.stderr.strip()}"
        )
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"herdr {' '.join(args)} 输出不是 JSON: {out[:400]}")
    if "error" in payload:
        raise RuntimeError(f"herdr {' '.join(args)} 报错: {payload['error']}")
    return payload.get("result", payload)


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding="utf-8")
        log(f"已生成默认配置 {CONFIG_PATH}")
    if tomllib is None:
        log("缺少 tomllib：需要 python 3.11+，或 pip install tomli")
        sys.exit(1)
    try:
        with CONFIG_PATH.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:  # 配置写坏了就别执行，免得新 tab 里跑错东西
        log(f"配置解析失败，跳过本次执行: {exc}")
        sys.exit(0)


def event_payload() -> dict:
    """HERDR_PLUGIN_EVENT_JSON 形如 {"event":"tab_created","data":{"type":...,"tab":{...}}}。"""
    raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON") or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    for key in ("data", "event"):
        if isinstance(data.get(key), dict):
            data = data[key]
    return data


def event_tab() -> dict:
    return event_payload().get("tab") or {}


INITIAL_TAB_TTL = 120.0


def initial_tabs_file() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / "initial-tabs.txt"


def mark_initial_tab(tab_id: str) -> None:
    """workspace.created 时记下它的初始 tab，只跳过这一个。"""
    if not tab_id:
        return
    path = initial_tabs_file()
    now = time.time()
    keep = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and now - float(parts[1]) < INITIAL_TAB_TTL:
                keep.append(line)
    keep.append(f"{tab_id} {now}")
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")


def consume_initial_tab(tab_id: str) -> bool:
    path = initial_tabs_file()
    if not path.exists() or not tab_id:
        return False
    now = time.time()
    keep, hit = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            ts = float(parts[1])
        except ValueError:
            continue
        if parts[0] == tab_id:
            hit = True
            continue
        if now - ts < INITIAL_TAB_TTL:
            keep.append(line)
    path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return hit


def skip_initial_workspace_tab(cfg: dict, tab: dict) -> bool:
    """只放过新建 workspace 的那一个初始 tab；同一 workspace 里之后手开的 tab 照常执行。"""
    if str(cfg.get("new_workspace") or "skip").strip().lower() == "run":
        return False
    if consume_initial_tab(str(tab.get("tab_id") or "")):
        log("这是新建 workspace 的初始 tab，按 new_workspace=skip 不处理")
        return True
    if tab.get("number") == 1:
        log("这是 workspace 的首个 tab（number=1），按 new_workspace=skip 不处理")
        return True
    return False


def target_tab(manual: bool) -> dict:
    """返回 {tab_id, workspace_id, label}，事件和动作两条路径都走它。"""
    tab = event_tab()
    if not manual and tab.get("tab_id"):
        return tab
    tab_id = os.environ.get("HERDR_ACTIVE_TAB_ID") or os.environ.get("HERDR_TAB_ID") or ""
    out = {
        "tab_id": tab_id,
        "workspace_id": tab.get("workspace_id")
        or os.environ.get("HERDR_ACTIVE_WORKSPACE_ID")
        or os.environ.get("HERDR_WORKSPACE_ID", ""),
        "label": tab.get("label", ""),
    }
    return out


def tab_panes(tab_id: str, workspace_id: str) -> list:
    panes = herdr("pane", "list").get("panes", [])
    hits = [p for p in panes if p.get("tab_id") == tab_id]
    if not hits and workspace_id:
        panes = herdr("pane", "list", "--workspace", workspace_id).get("panes", [])
        hits = [p for p in panes if p.get("tab_id") == tab_id]
    return hits


def wait_root_pane(tab_id: str, workspace_id: str, timeout_s: float = 5.0) -> dict | None:
    """事件可能比 pane 早到，重试一会儿。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hits = tab_panes(tab_id, workspace_id)
        if hits:
            return hits[0]
        time.sleep(0.1)
    return None


def repo_name_of(cwd: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return os.path.basename(proc.stdout.strip())
    except Exception:
        pass
    return ""


def workspace_label_of(workspace_id: str) -> str:
    try:
        for ws in herdr("workspace", "list").get("workspaces", []):
            if ws.get("workspace_id") == workspace_id:
                return ws.get("label", "")
    except Exception:
        pass
    return ""


def rule_matches(rule: dict, ctx: dict) -> bool:
    for key, value in rule.items():
        if key in ("name", "mode", "kind", "command", "agent_args", "ready_timeout_ms", "label"):
            continue
        want = value if isinstance(value, list) else [value]
        have = ctx.get(key, "")
        if not any(
            fnmatch.fnmatchcase(str(have), os.path.expanduser(str(w)))
            if key == "cwd_glob"
            else str(have) == str(w)
            for w in want
        ):
            return False
    return True


def resolve_run(cfg: dict, ctx: dict) -> tuple[dict, str]:
    for idx, rule in enumerate(cfg.get("rules", [])):
        if rule_matches(rule, ctx):
            merged = {**cfg.get("default", {}), **rule}
            return merged, rule.get("name") or f"规则#{idx + 1}"
    return dict(cfg.get("default", {})), "默认"


def warn_repo_rules(cfg: dict, ctx: dict) -> None:
    """规则里用到 repo_name 但取不到仓库名时，提醒一句，免得静默落到默认动作。"""
    if ctx.get("repo_name"):
        return
    if any("repo_name" in rule for rule in cfg.get("rules", [])):
        log("注意: 当前 cwd 取不到 git 仓库名（没装 git 或不在 git 仓库里），"
            "按 repo_name 写的那几条规则不会命中")


def wait_shell_ready(pane_id: str, timeout_ms: int) -> bool:
    """新 pane 的提示符出来之前写进去的字会被吞掉，所以等前台进程变回 shell 本身。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            info = herdr("pane", "process-info", "--pane", pane_id)["process_info"]
        except Exception:
            info = {}
        shell_pid = info.get("shell_pid")
        if shell_pid and info.get("foreground_process_group_id") == shell_pid:
            return True
        time.sleep(READY_POLL_MS / 1000)
    return False


def agent_name_for(kind: str, pane_id: str) -> str:
    digest = hashlib.sha1(pane_id.encode()).hexdigest()[:6]
    return f"{kind}-{digest}"[:32]


def start_agent(pane_id: str, run: dict) -> None:
    kind = str(run.get("kind") or "hermes")
    name = str(run.get("label") or agent_name_for(kind, pane_id))
    extra = list(run.get("agent_args") or [])
    args = [HERDR, "agent", "start", name, "--kind", kind, "--pane", pane_id,
            "--timeout", "30000"]
    if extra:
        args += ["--", *[str(a) for a in extra]]
    last = ""
    for attempt in range(12):
        proc = subprocess.run(args, capture_output=True, text=True, timeout=330)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode == 0 and '"error"' not in out:
            log(f"agent {name}({kind}) 已在 {pane_id} 启动")
            return
        last = out
        if any(marker in out for marker in BUSY_MARKERS):
            time.sleep(0.25)
            continue
        break
    raise RuntimeError(f"agent start 失败: {last[:400]}")


def run_command(pane_id: str, command: str) -> None:
    proc = subprocess.run(
        [HERDR, "pane", "run", pane_id, command], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pane run 失败: {((proc.stdout or '') + (proc.stderr or '')).strip()[:400]}"
        )
    log(f"已在 {pane_id} 执行: {command}")


def seen_recently(tab_id: str) -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "seen.txt"
    now = time.time()
    keep = []
    hit = False
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                ts = float(parts[1])
            except ValueError:
                continue
            if now - ts > 600:
                continue
            if parts[0] == tab_id and now - ts < 120:
                hit = True
            keep.append(line)
    keep.append(f"{tab_id} {now}")
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    return hit


def disabled_flag() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / "disabled"


def own_file() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / "own-tabs.txt"


def mark_own(tab_id: str) -> None:
    """记住本插件通过选择器新建的 tab，避免事件钩子再往里塞一遍默认 agent。"""
    path = own_file()
    now = time.time()
    keep = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and now - float(parts[1]) < 120:
                keep.append(line)
    keep.append(f"{tab_id} {now}")
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")


def consume_own(tab_id: str) -> bool:
    path = own_file()
    if not path.exists():
        return False
    now = time.time()
    keep, hit = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            ts = float(parts[1])
        except ValueError:
            continue
        if parts[0] == tab_id and now - ts < 120:
            hit = True
            continue
        if now - ts < 120:
            keep.append(line)
    path.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    return hit


def choices(cfg: dict) -> list:
    """选择器选项。选项没写的字段从 [default] 继承（尤其 agent_args，否则 --tui 之类会丢）。"""
    rows = cfg.get("choices") or DEFAULT_CHOICES
    base = dict(cfg.get("default") or {})
    out = []
    for row in rows:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        item = {
            "label": label,
            "mode": str(row.get("mode") or base.get("mode") or "skip"),
            "kind": str(row.get("kind") or base.get("kind") or ""),
            "command": str(row.get("command") or ""),
            "ready_timeout_ms": int(row.get("ready_timeout_ms") or base.get("ready_timeout_ms") or 8000),
        }
        # agent_args 只在「和 [default] 同一个 kind」时继承：--tui 这类参数不能套到别的 agent 上
        same_kind = bool(item["kind"]) and item["kind"] == str(base.get("kind") or "")
        raw_args = row.get("agent_args")
        if raw_args is None:
            raw_args = (base.get("agent_args") or []) if same_kind else []
        item["agent_args"] = [str(a) for a in raw_args]
        # mode=command / skip 的选项不该继承 agent 的 kind 和参数
        if item["mode"] != "agent":
            item["kind"] = str(row.get("kind") or "")
            item["agent_args"] = []
        out.append(item)
    return out


def print_choices(cfg: dict) -> None:
    """给选择器用：每行 `标签<TAB>说明`。"""
    for row in choices(cfg):
        if row["mode"] == "agent":
            hint = f"agent:{row['kind']}"
        elif row["mode"] == "command":
            hint = row["command"]
        else:
            hint = "shell"
        print(f"{row['label']}\t{hint}")


def pick_builtin(rows: list) -> str | None:
    """不依赖 fzf 的单键选择器：↑↓/j k 移动，回车确认，Esc 取消，数字快速选。"""
    import select
    import termios

    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        log("没有可用的终端，无法交互选择")
        return None

    def hint_of(row: dict) -> str:
        if row["mode"] == "agent":
            return f"agent:{row['kind']}"
        if row["mode"] == "command":
            return row["command"]
        return "shell"

    old = termios.tcgetattr(tty_fd)
    idx, draw = 0, True
    try:
        new = termios.tcgetattr(tty_fd)
        new[3] &= ~(termios.ECHO | termios.ICANON)
        new[6][termios.VMIN] = 1
        new[6][termios.VTIME] = 0
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, new)
        while True:
            if draw:
                buf = ["\x1b[H\x1b[2J",
                       "新 tab 执行什么？  ↑↓/jk 选择 · 回车确认 · Esc 取消\n\n"]
                for i, row in enumerate(rows):
                    line = f" {i + 1}. {row['label']:<20} {hint_of(row)}"
                    buf.append(("\x1b[7m" + line + "\x1b[0m\n") if i == idx else line + "\n")
                sys.stdout.write("".join(buf))
                sys.stdout.flush()
                draw = False
            ready, _, _ = select.select([tty_fd], [], [], 0.25)
            if not ready:
                continue
            ch = os.read(tty_fd, 1)
            if not ch:
                return None
            if ch in (b"\r", b"\n"):
                return rows[idx]["label"]
            if ch == b"\x1b":
                more, _, _ = select.select([tty_fd], [], [], 0.05)
                if not more:
                    return None
                seq = os.read(tty_fd, 2)
                if seq == b"[A":
                    idx = (idx - 1) % len(rows)
                    draw = True
                elif seq == b"[B":
                    idx = (idx + 1) % len(rows)
                    draw = True
                continue
            if ch == b"k":
                idx = (idx - 1) % len(rows)
                draw = True
            elif ch == b"j":
                idx = (idx + 1) % len(rows)
                draw = True
            elif ch.isdigit() and 1 <= int(ch) <= len(rows):
                return rows[int(ch) - 1]["label"]
    finally:
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, old)
        os.close(tty_fd)
        sys.stdout.write("\n")
        sys.stdout.flush()


def do_pick(cfg: dict, execute: bool, dry_run: bool) -> int:
    rows = choices(cfg)
    if not rows:
        log("没有可选项")
        return 0
    label = pick_builtin(rows)
    if not label:
        return 0
    if dry_run:
        print(label)
        return 0
    return do_new(cfg, label) if execute else 0


def picker_mode(cfg: dict) -> str:
    """env TAB_AUTORUN_PICKER > config picker > auto。"""
    mode = (os.environ.get("TAB_AUTORUN_PICKER") or cfg.get("picker") or "auto").strip().lower()
    if mode not in ("auto", "builtin", "fzf"):
        mode = "auto"
    if mode == "auto":
        import shutil
        return "fzf" if shutil.which("fzf") else "builtin"
    if mode == "fzf":
        import shutil
        if not shutil.which("fzf"):
            log("配置要求 fzf 但没找到，改用内置菜单")
            return "builtin"
    return mode


def deps_report() -> str:
    import shutil
    py = ".".join(str(n) for n in sys.version_info[:3])
    fzf = shutil.which("fzf")
    return (
        f"python {py} · tomllib={'ok' if tomllib else '缺少(需 3.11+ 或 pip install tomli)'} · "
        f"fzf={fzf or '未安装(选择器会用内置菜单)'} · herdr={HERDR}"
    )


def do_new(cfg: dict, label: str) -> int:
    """选择器选定之后：新建 tab 并在里面执行。"""
    row = next((c for c in choices(cfg) if c["label"] == label), None)
    if row is None:
        log(f"没有叫 {label!r} 的选项")
        return 1
    created = herdr("tab", "create", "--focus")
    pane = created.get("root_pane", {})
    tab = created.get("tab", {})
    pane_id, tab_id = pane.get("pane_id", ""), tab.get("tab_id", "")
    mark_own(tab_id)
    log(f"新建 {tab_id} → {label} (mode={row['mode']})")
    if row["mode"] == "skip":
        return 0
    if not wait_shell_ready(pane_id, int(row.get("ready_timeout_ms") or 8000)):
        log(f"{pane_id} 的 shell 在超时内没就绪，放弃")
        return 0
    try:
        if row["mode"] == "agent":
            start_agent(pane_id, {"kind": row["kind"] or "hermes",
                                  "agent_args": row["agent_args"]})
        else:
            run_command(pane_id, row["command"])
    except Exception as exc:
        log(f"执行失败: {exc}")
        return 1
    return 0


def do_toggle() -> None:
    flag = disabled_flag()
    if flag.exists():
        flag.unlink()
        log("已开启：新 tab 会自动起 agent")
    else:
        flag.write_text("1\n", encoding="utf-8")
        log("已关闭：新 tab 保持裸 shell（再执行一次 toggle 恢复）")


def main(argv: list[str]) -> int:
    if "--toggle" in argv:
        do_toggle()
        return 0

    manual = "--manual" in argv
    doctor = "--doctor" in argv

    cfg = load_config()
    if "--workspace-created" in argv:
        ws = event_payload().get("workspace") or {}
        initial = str(ws.get("active_tab_id") or os.environ.get("HERDR_TAB_ID", ""))
        mark_initial_tab(initial)
        log(f"记录新 workspace {ws.get('workspace_id')} 的初始 tab {initial}（只跳过它）")
        return 0
    if "--picker-mode" in argv:
        print(picker_mode(cfg))
        return 0
    if "--pick-exec" in argv or "--pick" in argv:
        return do_pick(cfg, execute="--pick-exec" in argv, dry_run="--dry-run" in argv)
    if "--choices" in argv:
        print_choices(cfg)
        return 0
    if "--new" in argv:
        idx = argv.index("--new")
        if idx + 1 >= len(argv):
            log("--new 需要一个选项名")
            return 1
        return do_new(cfg, argv[idx + 1])

    if not doctor:
        if not cfg.get("enabled", True):
            log("配置里 enabled = false，跳过")
            return 0
        if disabled_flag().exists():
            log("已通过 toggle 关闭，跳过")
            return 0

    tab = target_tab(manual)
    tab_id, tab_label = tab.get("tab_id", ""), tab.get("label", "")
    workspace_id = tab.get("workspace_id", "")
    if not tab_id:
        if doctor:
            log(deps_report())
            log(f"选项: {[row['label'] for row in choices(cfg)]} · picker={picker_mode(cfg)}")
            return 0
        log("拿不到 tab_id，跳过")
        return 0

    if not manual and skip_initial_workspace_tab(cfg, tab):
        return 0

    if not manual and consume_own(tab_id):
        log(f"{tab_id} 是选择器刚建的 tab，事件钩子不再插手")
        return 0

    pane = wait_root_pane(tab_id, workspace_id)
    if pane is None:
        log(f"{tab_id} 找不到 pane，跳过")
        return 0
    pane_id = pane.get("pane_id", "")

    if pane.get("agent"):
        log(f"{tab_id} 的 pane 已经跑着 agent={pane['agent']}，跳过")
        return 0

    cwd = pane.get("cwd") or pane.get("foreground_cwd") or os.environ.get("HERDR_ACTIVE_PANE_CWD", "")
    ctx = {
        "cwd": cwd,
        "repo_name": repo_name_of(cwd) if cwd else "",
        "workspace_label": workspace_label_of(workspace_id or pane.get("workspace_id", "")),
        "tab_label": tab_label,
    }
    warn_repo_rules(cfg, ctx)
    run, origin = resolve_run(cfg, ctx)
    mode = str(run.get("mode") or "skip")
    log(f"{tab_id} 匹配 {origin} → mode={mode} cwd={cwd} repo={ctx['repo_name']}")

    if doctor:
        log(f"上下文: {json.dumps(ctx, ensure_ascii=False)}")
        log(f"将执行: {json.dumps(run, ensure_ascii=False)}")
        log(deps_report())
        return 0

    if mode == "skip":
        return 0

    if not manual and seen_recently(tab_id):
        log(f"{tab_id} 两分钟内已处理过，跳过重复事件")
        return 0

    if not wait_shell_ready(pane_id, int(run.get("ready_timeout_ms") or 8000)):
        log(f"{pane_id} 的 shell 在超时内没就绪，放弃")
        return 0

    # 等 shell 的过程中 herdr 可能已经把 pane 恢复成某个 agent 了
    try:
        fresh = herdr("pane", "get", pane_id).get("pane", {})
        if fresh.get("agent"):
            log(f"{pane_id} 已被 herdr 认领为 agent={fresh['agent']}，跳过")
            return 0
    except Exception:
        pass

    try:
        if mode == "agent":
            start_agent(pane_id, run)
        elif mode == "command":
            command = str(run.get("command") or "").strip()
            if not command:
                log("mode=command 但 command 为空，跳过")
                return 0
            run_command(pane_id, command)
        else:
            log(f"未知 mode={mode}，跳过")
    except Exception as exc:
        log(f"执行失败: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
