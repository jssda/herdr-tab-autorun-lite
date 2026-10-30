# herdr-tab-autorun-lite

[中文](README.md) | **English**

Run a command or start a coding agent (hermes / opencode / claude / codex …) automatically in every
new herdr tab, so you never type `hermes` again. A `prefix+t` popup picker covers the cases where you
want to choose manually.

- Pure python3 standard library — **no** npm, node-gyp or native modules. Install and go.
- Two actions: `herdr agent start` (herdr launches the agent and tracks its state) or `pane run`
  (any command inside the shell).
- Rules match on cwd / git repo / workspace label / tab label, so each project can get its own agent.
- Optional fzf menu; without fzf it falls back to a built-in python menu with the same features.

## Requirements

Required:

| Requirement | Used for | Notes |
|---|---|---|
| python ≥ 3.11 | the plugin itself | reads config with stdlib `tomllib`; on 3.9/3.10 `pip install tomli` works too (fallback import is built in) |
| herdr ≥ 0.9.0 | host | uses the `tab.created` event hook, `tab create`, `pane list/get/process-info`, `agent start`, `pane run`, `plugin pane open` |
| POSIX sh | two entry scripts | `picker.sh` / `open-picker.sh`, plus `printf` and `cut` |

Optional:

| Requirement | Used for | Without it |
|---|---|---|
| `fzf` | searchable picker menu | falls back to the built-in python menu (↑↓ / jk / digits + Enter) |
| `git` | `repo_name` rule matching (`git -C <cwd> rev-parse --show-toplevel`) | only `repo_name` predicates are affected; `cwd_glob` keeps working, and rules that use `repo_name` log a warning instead of matching |

No node / npm / bun / third-party python packages. Check your machine:

```sh
python3 autorun.py --doctor     # python version, tomllib, fzf, herdr path, picker mode
```

## Install

```sh
herdr plugin install jssda/herdr-tab-autorun-lite
herdr plugin list
herdr plugin config-dir jssd.tab-autorun      # prints the config directory
```

For local development, `link` instead (no build step; code changes apply on the next event):

```sh
git clone https://github.com/jssda/herdr-tab-autorun-lite
herdr plugin link ./herdr-tab-autorun-lite
```

## Quick start

The config file is `config.toml` inside the directory printed by `herdr plugin config-dir
jssd.tab-autorun`. It is generated on first use. Edits apply immediately — no reload, no restart.

```toml
enabled = true
picker = "auto"          # auto = fzf when available; builtin = always the built-in menu; fzf = require fzf

[default]
mode = "agent"           # agent | command | skip
kind = "hermes"          # hermes / opencode / claude / codex ...
command = ""             # command line used when mode = "command"
agent_args = []          # extra args for the agent, e.g. ["--model", "deepseek-flash"]
ready_timeout_ms = 8000  # how long to wait for the new pane's shell prompt
```

Open a new tab and it should land straight in hermes. To check rule matching first:

```sh
herdr plugin action invoke jssd.tab-autorun.doctor
herdr plugin log list --plugin jssd.tab-autorun
```

## Per-project rules

`[[rules]]` are evaluated top to bottom; the first rule whose predicates all match wins.

```toml
[[rules]]
name = "frontend repo uses opencode"
cwd_glob = "~/code/web-*"      # * crosses /, ** also works
repo_name = "web-frontend"     # git repo root basename; a string or an array of strings

[[rules]]
name = "keep this directory a plain shell"
cwd_glob = "~/code/scratch/**"
mode = "skip"
```

Supported predicates:

| Predicate | Meaning |
|---|---|
| `cwd_glob` | glob against the pane cwd (supports `~`, `*`, `**`, `?`) |
| `repo_name` | git repo root directory name, exact match |
| `workspace_label` | herdr workspace label, exact match |
| `tab_label` | tab label, exact match |

With no rules at all, everything uses `[default]`.

## Picker (prefix+t)

Bind a key to pick per tab instead of taking the default:

```toml
# ~/.config/herdr/config.toml
[[keys.command]]
key = "prefix+t"
type = "plugin_action"
command = "jssd.tab-autorun.new"
description = "new tab: pick agent"
```

After `herdr config check` and `herdr server reload-config`, `prefix+t` opens the menu; picking an
entry creates the tab and runs the choice inside it. Esc cancels without creating a tab.

Define your own entries (omit `[[choices]]` to get the built-in hermes / opencode / plain shell):

```toml
[[choices]]
label = "hermes"
mode = "agent"
kind = "hermes"

[[choices]]
label = "hermes with a specific model"
mode = "agent"
kind = "hermes"
agent_args = ["--model", "deepseek-flash"]

[[choices]]
label = "dev server"
mode = "command"
command = "npm run dev"

[[choices]]
label = "plain shell"
mode = "skip"
```

Popup size lives in `herdr-plugin.toml` under `[[panes]] width/height` (70% × 50% by default).

`picker` and the built-in menu: with `fzf` installed the default is fzf (searchable); without fzf or
with `picker = "builtin"` you get the built-in menu (`↑↓` / `j k` to move, digits to jump, Enter to
confirm, Esc to cancel). To preview it without a popup:

```sh
python3 autorun.py --pick --dry-run   # prints the selection, creates nothing
python3 autorun.py --pick-exec        # really creates the tab and runs it
```

`TAB_AUTORUN_PICKER=auto|builtin|fzf` overrides the config, but only for scripts you run yourself —
the popup is spawned by the herdr server and inherits the server's environment, not your shell exports.

## Actions

| Action | What it does |
|---|---|
| `jssd.tab-autorun.new` | open the picker popup (usually bound to a key) |
| `jssd.tab-autorun.run-here` | apply the default rule to the current tab once |
| `jssd.tab-autorun.doctor` | print context, the matched rule and dependency status; executes nothing |
| `jssd.tab-autorun.toggle` | turn automatic runs on/off (bindable) |

```sh
herdr plugin action invoke jssd.tab-autorun.toggle
herdr plugin action list --plugin jssd.tab-autorun
herdr plugin log list --plugin jssd.tab-autorun
```

## How it works

```
tab.created event ──► herdr runs autorun.py --event
                       ├─ tab_id from $HERDR_PLUGIN_EVENT_JSON
                       ├─ find the tab's root pane via pane list (event can precede the pane; retry 5s)
                       ├─ pane already has an agent ⇒ skip (never fight herdr's own restore)
                       ├─ match rules on cwd / repo_name / workspace_label / tab_label
                       ├─ poll pane process-info until foreground pgid == shell_pid (prompt ready)
                       ├─ mode=agent   ⇒ herdr agent start <kind> --pane <id>
                       └─ mode=command ⇒ herdr pane run <id> "<command>"
```

The subtle part: **characters written into a brand-new pane before its first prompt appear are
swallowed**, so the script waits until `foreground_process_group_id == shell_pid`; otherwise the
command silently disappears.

The picker takes another path: `plugin_action` → popup pane → menu → `tab create --focus` → the new
tab is recorded (so the event hook skips it instead of injecting the default agent as well) → wait
for the prompt → run.

## Known limits

- Only new **tabs** are handled; pane splits are not.
- Tabs restored after a herdr server restart are not triggered (herdr already owns those panes).
- New worktrees / `workspace create` also emit `tab.created`; exclude them with `cwd_glob` +
  `mode = "skip"`.
- `mode = "agent"` depends on kinds `herdr agent start` knows; use `mode = "command"` for anything else.
- Only tested on Linux with herdr 0.9.5, which is why `platforms` declares `linux` only.

## Uninstall

```sh
herdr plugin uninstall jssd.tab-autorun
```

The config directory and logs are left in place; delete them manually if you want a clean slate.

## License

MIT — see [LICENSE](LICENSE).
