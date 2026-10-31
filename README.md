# 沙盒工具 (sandbox_tool)

通用沙盒工具：读写任意文件、执行任意命令（执行前会向用户请求确认），操作日志持久化到磁盘。

## 背景

本插件使用 Python 标准库（pathlib / subprocess / json）在 N.E.K.O. 插件内部构建了一个沙盒层，提供类似的能力。

> **注意**：本插件的权限等同于 N.E.K.O. 进程（当前用户）。它可以读写“所有文件”（不排除系统目录、N.E.K.O. 自身、用户文档），并执行“所有命令”。**没有原生的沙盒策略**；安全边界完全依赖于插件内部的确认机制和你的使用习惯。请谨慎使用。

## 文件结构

```
sandbox_tool/
|-- plugin.toml            # 插件清单
|-- __init__.py            # 插件主体（6 个入口点）
|-- data/
|   |-- settings.json      # 配置文件
|   \-- pending_commands.json  # 待确认命令持久化（运行时生成）
|-- logs/
|   \-- sandbox_tool.log   # 操作日志（运行时生成）
|-- static/
|   \-- index.html         # 面板
\-- README.md              # 本文件
```

## 入口点说明

| 入口 ID | 名称 | 描述 | 是否需要确认 |
|----------|------|-------------|----------------------|
| a_readfile | 读取文件 | 读取任意文件，按 utf-8/gbk/latin-1 顺序尝试解码，默认限制 2MB | 否 |
| b_writefile | 写入文件 | 写入任意文件，自动创建父目录 | 否 |
| c_listdir | 列出目录 | 列出任意目录下的文件和子目录 | 否 |
| d_command | 提交命令 | 提交命令执行；白名单命令直接运行，危险命令返回待确认状态，需要 e_confirm 批准后才能执行 | 仅危险命令 |
| e_confirm | 确认执行命令 | 用户同意后由 subprocess 执行，输出截断至 5000 字符 | - |
| f_logs | 查看日志 | 读取操作日志，返回最后 tail 行 | 否 |

## 方案：命令执行确认流程

1. 调用 `d_command` 提交命令（`cmd`、`timeout`），返回 `command_id`。
2. 插件通过 `push_message` 向用户推送待确认消息，并将命令持久化到 `data/pending_commands.json`。
3. 用户调用 `e_confirm`，传入 `command_id` 和 `confirm=true/false`。
4. 当 `confirm=true` 时，命令通过 `subprocess.run(shell=True)` 实际执行，记录退出码 / stdout / stderr。当 `confirm=false` 时，拒绝执行。
5. 重启后，未确认的命令会从 `pending_commands.json` 中恢复。

## 命令确认机制（前端必读）

`d_command` 不一定立即执行；有三种情况：

1. 白名单命令（dir, ver, echo, whoami, date, time, hostname, systeminfo, tasklist, ipconfig, ping, type, find, findstr, ls, pwd, cat, head, tail, grep, ps, df, du, uname, uptime, id 等）：直接执行，立即返回结果，无需用户确认。
2. 危险命令（匹配 del, erase, rmdir, rd, rm, format, fdisk, shutdown, restart, reboot, halt, poweroff, > , >> , | , sudo, runas, net user, reg add, reg delete, sc delete, sc stop 等）：返回状态 `pending_confirm` 并附带 `command_id`，命令持久化到 `data/pending_commands.json`，不会直接执行。前端应将 `command_id` 传给用户，由用户调用 `e_confirm` 并传入 `command_id` 和 `confirm:true`，才会通过 `subprocess.run` 实际执行。
3. 关闭确认机制（设置 `confirm_required` 为 false，不推荐）：所有命令直接执行。

前端处理建议：调用 `d_command` 后，如果返回的 `status === "pending_confirm"`，不要直接显示执行结果；应提示用户进行确认；收到用户同意后，再次调用 `e_confirm`。

## 日志

每次操作都会向 `logs/sandbox_tool.log` 追加一行 JSON，包含 `ts`、`op`、`target`、`result`；命令操作还包含 `confirmed`。超过 `max_log_lines` 时保留后半部分。

## 安全边界

- 没有原生沙盒策略，权限等同于 N.E.K.O. 进程（当前用户）。
- 命令执行必须经过 `e_confirm` 确认（除非将 `confirm_required` 设为 false，不推荐）。
- 命令输出截断至 5000 字符，避免体积过大。
- 日志写入英文路径 `logs/`，避免中文编码问题。

## 使用示例（面板）

打开 N.E.K.O. 插件面板“沙盒工具面板”，你可以直接：
- 读取任意文件（路径 + 最大字节数）
- 写入任意文件（路径 + 内容 + 是否创建父目录）
- 列出任意目录
- 提交命令 -> 获取命令 ID -> 在“待确认命令”中同意/拒绝
- 查看操作日志

## 开发

所有代码仅使用 Python 标准库，无第三方依赖。插件目录下无需 `.venv`。

```
uv sync   # 可选，本插件无第三方依赖
```

## 许可证

个人使用，无特殊许可证。
```
