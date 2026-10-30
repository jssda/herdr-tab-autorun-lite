# herdr-tab-autorun-lite

**中文** | [English](README.en.md)

新开 herdr tab 时自动跑一条命令或起一个 coding agent（hermes / opencode / claude / codex …），
省掉每次手敲 `hermes` 那一下。再配一个 `prefix+t` 弹窗选择器，想手动挑的时候用。

- 纯 python3 标准库，**没有** npm / node-gyp / 原生模块，装完即用
- 两种动作：`herdr agent start`（herdr 自己拉起 agent 并识别状态）或 `pane run`（任意命令）
- 按 cwd / git 仓库 / workspace 标签 / tab 标签匹配规则，不同项目起不同 agent
- 可选 fzf 菜单；没有 fzf 会用内置 python 菜单，功能不缺

## 依赖

必装：

| 依赖 | 用途 | 备注 |
|---|---|---|
| python ≥ 3.11 | 插件本体 | 用标准库 `tomllib` 读配置；3.9/3.10 装 `pip install tomli` 也能跑（代码里有回退） |
| herdr ≥ 0.9.0 | 宿主 | 用到 `tab.created` 事件钩子、`tab create`、`pane list/get/process-info`、`agent start`、`pane run`、`plugin pane open` |
| POSIX sh | 两个入口脚本 | `picker.sh` / `open-picker.sh`，外加 `printf`、`cut` |

可选：

| 依赖 | 用途 | 没有会怎样 |
|---|---|---|
| `fzf` | 选择器的可搜索菜单 | 自动退化成内置 python 菜单（↑↓ / jk / 数字 + 回车） |
| `git` | 规则里 `repo_name` 匹配（`git -C <cwd> rev-parse --show-toplevel`） | 只影响 `repo_name` 条件，`cwd_glob` 照常用；用到 `repo_name` 的规则会不命中并打一条警告 |

不需要 node / npm / bun / 任何第三方 python 包。检查本机环境：

```sh
python3 autorun.py --doctor     # python 版本、tomllib 是否可用、fzf、herdr 路径、picker 模式
```

## 安装

```sh
herdr plugin install jssda/herdr-tab-autorun-lite
herdr plugin list
herdr plugin config-dir jssd.tab-autorun      # 打印配置目录
```

本地开发用 `link`（不跑 build，改完代码下一次事件直接生效）：

```sh
git clone https://github.com/jssda/herdr-tab-autorun-lite
herdr plugin link ./herdr-tab-autorun-lite
```

## 快速开始

配置文件在 `herdr plugin config-dir jssd.tab-autorun` 打印的目录下，文件名 `config.toml`，
首次触发会自动生成。改完立即生效，不用重启 herdr。

```toml
enabled = true
picker = "auto"          # auto = 有 fzf 用 fzf；builtin = 永远内置菜单；fzf = 只认 fzf

[default]
mode = "agent"           # agent | command | skip
kind = "hermes"          # hermes / opencode / claude / codex ...
command = ""             # mode="command" 时执行的命令行
agent_args = []          # 传给 agent 的参数，例如 ["--model", "deepseek-flash"]
ready_timeout_ms = 8000  # 等新 pane 的 shell 提示符就绪的上限
```

保存后新开一个 tab 就应该直接进 hermes。想先确认规则匹配对不对：

```sh
herdr plugin action invoke jssd.tab-autorun.doctor
herdr plugin log list --plugin jssd.tab-autorun
```

## 按项目分流

`[[rules]]` 从上到下匹配，第一条命中生效；一条规则里出现的所有条件必须同时满足。

```toml
[[rules]]
name = "前端仓用 opencode"
cwd_glob = "~/code/web-*"      # * 会跨越 /，也可以写 **
repo_name = "web-frontend"     # git 仓库根目录名；也可以是字符串数组

[[rules]]
name = "这个目录保持裸 shell"
cwd_glob = "~/code/scratch/**"
mode = "skip"
```

可用条件：

| 条件 | 含义 |
|---|---|
| `cwd_glob` | 对 pane 的 cwd 做 glob（支持 `~`、`*`、`**`、`?`） |
| `repo_name` | git 仓库根目录名，精确匹配 |
| `workspace_label` | herdr workspace 标签，精确匹配 |
| `tab_label` | tab 标签，精确匹配 |

一条规则都没有时，全部走 `[default]`。

## 选择器（prefix+t）

不想每次都吃默认值？绑定一个键，弹出菜单手选：

```toml
# ~/.config/herdr/config.toml
[[keys.command]]
key = "prefix+t"
type = "plugin_action"
command = "jssd.tab-autorun.new"
description = "新建 tab 并选择执行哪个 agent"
```

`herdr config check` + `herdr server reload-config` 之后按 `prefix+t` 就会弹出选择菜单，
选中后新建 tab 并在里面执行；Esc 取消，不建 tab。

选项自己定（不写则用内置的 hermes / opencode / 裸 shell）：

```toml
[[choices]]
label = "hermes"
mode = "agent"
kind = "hermes"

[[choices]]
label = "hermes + 指定模型"
mode = "agent"
kind = "hermes"
agent_args = ["--model", "deepseek-flash"]

[[choices]]
label = "dev server"
mode = "command"
command = "npm run dev"

[[choices]]
label = "裸 shell"
mode = "skip"
```

弹窗尺寸改 `herdr-plugin.toml` 里的 `[[panes]] width/height`（默认 70% × 50%）。

`picker` 与内置菜单：`fzf` 存在时默认用 fzf（可搜索）；没有 fzf 或 `picker = "builtin"` 时走
内置菜单（`↑↓`/`j k` 移动、数字直选、回车确认、Esc 取消）。想不弹窗先看看内置菜单：

```sh
python3 autorun.py --pick --dry-run   # 选中只打印，不建 tab
python3 autorun.py --pick-exec        # 选中就真的建 tab 并执行
```

环境变量 `TAB_AUTORUN_PICKER=auto|builtin|fzf` 优先级高于配置，但它只影响**你自己直接运行**的脚本——
popup 由 herdr server 拉起、继承 server 的环境，看不到你当前 shell 的 export。

## 动作

| 动作 | 作用 |
|---|---|
| `jssd.tab-autorun.new` | 打开选择器弹窗（一般绑到按键） |
| `jssd.tab-autorun.run-here` | 在当前 tab 补跑一次默认规则 |
| `jssd.tab-autorun.doctor` | 打印上下文、命中的规则和依赖情况，不执行 |
| `jssd.tab-autorun.toggle` | 开关自动运行（也可绑键） |

```sh
herdr plugin action invoke jssd.tab-autorun.toggle
herdr plugin action list --plugin jssd.tab-autorun
herdr plugin log list --plugin jssd.tab-autorun
```

## 工作原理

```
tab.created 事件 ──► herdr 运行 autorun.py --event
                       ├─ 读 $HERDR_PLUGIN_EVENT_JSON 拿 tab_id
                       ├─ pane list 找到该 tab 的根 pane（事件可能早于 pane，重试 5s）
                       ├─ pane 上已有 agent ⇒ 跳过（不抢 herdr 自己恢复的 agent）
                       ├─ 按 cwd / repo_name / workspace_label / tab_label 匹配规则
                       ├─ 轮询 pane process-info 到前台进程组 == shell_pid（提示符就绪）
                       ├─ mode=agent   ⇒ herdr agent start <kind> --pane <id>
                       └─ mode=command ⇒ herdr pane run <id> "<命令>"
```

关键点：**新 pane 的首个提示符出来之前写进去的字符会被吞掉**，所以必须先确认
`foreground_process_group_id == shell_pid`，否则命令会静默丢失。

选择器走另一条路：`plugin_action` → popup pane → 菜单 → `tab create --focus` → 记下新 tab
（事件钩子跳过它，避免再塞一个默认 agent）→ 等提示符 → 执行。

## 已知边界

- 只对新 **tab** 生效，不处理 pane split。
- herdr server 重启后恢复的 tab 不触发（此时 pane 已被 herdr 认领，本来也不该重跑）。
- 新建 worktree / `workspace create` 产生的 tab 同样会触发，用 `cwd_glob` + `mode = "skip"` 排除。
- `mode = "agent"` 依赖 `herdr agent start` 能识别的 kind；不认识的 CLI 用 `mode = "command"`。
- 目前只在 Linux + herdr 0.9.5 实测过；`platforms` 因此只声明 `linux`。

## 卸载

```sh
herdr plugin uninstall jssd.tab-autorun
```

配置文件与日志保留在 `herdr plugin config-dir jssd.tab-autorun` 指向的目录，需要的话手动删。

## 许可

MIT，见 [LICENSE](LICENSE)。
