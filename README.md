# Sandbox Tool (sandbox_tool)

General-purpose sandbox tool: read/write arbitrary files, execute arbitrary commands (prompt user confirmation before execution), operation log persisted to disk.

## Background

DSH's sandbox tools (read / write / pwsh / run_code) are Agent runtime-only; N.E.K.O. plugins cannot call them. This plugin builds a sandbox layer inside N.E.K.O. plugins using the Python standard library (pathlib / subprocess / json), providing similar capabilities.

> **Security warning**: This plugin's permissions equal the N.E.K.O. process (current user). It can read/write "all files" (not excluding system directories, N.E.K.O. itself, user documents) and execute "all commands". **There is no native sandbox policy**; the security boundary relies entirely on the plugin's internal confirmation mechanism and your usage habits. Use with caution.

## File Structure

```
sandbox_tool/
|-- plugin.toml            # Plugin manifest
|-- __init__.py            # Plugin body (6 entry points)
|-- data/
|   |-- settings.json      # Configuration
|   \-- pending_commands.json  # Pending command persistence (generated at runtime)
|-- logs/
|   \-- sandbox_tool.log   # Operation log (generated at runtime)
|-- static/
|   \-- index.html         # Panel
\-- README.md              # This file
```

## Entry Point Description

| Entry ID | Name | Description | Confirmation Required |
|----------|------|-------------|----------------------|
| a_readfile | Read File | Read arbitrary file, utf-8/gbk/latin-1 decoding fallback, default 2MB limit | No |
| b_writefile | Write File | Write arbitrary file, auto-create parent directories | No |
| c_listdir | List Directory | List files and subdirectories under any directory | No |
| d_command | Submit Command | Submit command execution; whitelist commands run directly, dangerous commands return pending confirmation, require e_confirm approval before execution | Dangerous commands only |
| e_confirm | Confirm Command Execution | User agrees then subprocess executes, output truncated to 5000 characters | - |
| f_logs | View Logs | Read operation log, return last tail lines | No |

## Option A: Command Execution Confirmation Flow

1. Call `d_command` to submit a command (`cmd`, `timeout`), returns `command_id`.
2. The plugin pushes a pending confirmation message to the user via `push_message`, and the command is persisted to `data/pending_commands.json`.
3. User calls `e_confirm` with `command_id` and `confirm=true/false`.
4. When `confirm=true`, the command is actually executed via `subprocess.run(shell=True)`, recording exit code / stdout / stderr. When `confirm=false`, execution is rejected.
5. After restart, unconfirmed commands are restored from `pending_commands.json`.

## Command Confirmation Mechanism (Frontend Must Read)

`d_command` does not necessarily execute immediately; there are three cases:

1. Whitelist commands (dir, ver, echo, whoami, date, time, hostname, systeminfo, tasklist, ipconfig, ping, type, find, findstr, ls, pwd, cat, head, tail, grep, ps, df, du, uname, uptime, id, etc.): execute directly, return result immediately, no user confirmation needed.
2. Dangerous commands (matching del, erase, rmdir, rd, rm, format, fdisk, shutdown, restart, reboot, halt, poweroff, > , >> , | , sudo, runas, net user, reg add, reg delete, sc delete, sc stop, etc.): return status pending_confirm with command_id, command persisted to data/pending_commands.json, not directly executed. The frontend should pass command_id to the user, who then calls e_confirm with command_id and confirm:true to actually execute via subprocess.run.
3. Disable confirmation mechanism (set confirm_required to false, not recommended): all commands execute directly.

Frontend handling suggestion: after calling d_command, if the returned status === "pending_confirm", do not display execution results directly; instead prompt the user to confirm; after receiving user consent, call e_confirm again.

## Configuration (data/settings.json)

```json
{
  "confirm_required": true,
  "command_timeout": 30,
  "log_path": "logs/sandbox_tool.log",
  "max_log_lines": 10000
}
```

- `confirm_required`: Whether confirmation is required before execution (default true, strongly recommended to keep true).
- `command_timeout`: Command timeout in seconds (default 30).
- `log_path`: Operation log path (default `logs/sandbox_tool.log`, English path avoids Chinese encoding issues).
- `max_log_lines`: Maximum log lines; exceeds retain latter half (default 10000).

## Logs

Each operation appends one JSON line to `logs/sandbox_tool.log`, containing `ts`, `op`, `target`, `result`; command operations also include `confirmed`. Exceeding `max_log_lines` retains the latter half.

## Security Boundary

- No native sandbox policy, permissions equal N.E.K.O. process (current user).
- Command execution must be confirmed via `e_confirm` (unless `confirm_required` is disabled, not recommended).
- Command output truncated to 5000 characters to avoid excessive size.
- Logs written to English path `logs/`, avoiding Chinese encoding issues.

## Usage Example (Panel)

Open the N.E.K.O. plugin panel "Sandbox Tool Panel", you can directly:
- Read any file (path + max bytes)
- Write any file (path + content + whether to create parent directories)
- List any directory
- Submit command -> get command ID -> agree/reject in "Pending Commands"
- View operation log

## Development

All code uses only Python standard library, no third-party dependencies. No `.venv` needed under the plugin directory.

```
uv sync   # optional, this plugin has no third-party dependencies
```

## License

Personal use, no special license.
