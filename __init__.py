import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from plugin.sdk.plugin import lifecycle, llm_tool, NekoPluginBase, neko_plugin, Ok, plugin_entry


LOG_DIR_NAME = "logs"
PENDING_DIR_NAME = "data"
PENDING_FILE = "pending_commands.json"

DEFAULTS = {
    "confirm_required": True,
    "command_timeout": 30,
    "log_path": "logs/sandbox_tool.log",
    "max_log_lines": 10000,
    "auto_execute_safe": True,
}

# Safe command whitelist: executed directly, no user confirmation required
SAFE_COMMANDS = [
    "dir", "ver", "echo", "whoami", "date", "time", "cd", "tree",
    "hostname", "systeminfo", "tasklist", "ipconfig", "ping",
    "type", "find", "findstr", "cls", "prompt", "set", "path",
    "ls", "pwd", "cat", "head", "tail", "grep", "ps", "df", "du",
    "uname", "uptime", "id",
]

# Dangerous command keywords: matching any one requires user confirmation
DANGEROUS_KEYWORDS = [
    "del ", "erase ", "rmdir ", "rd ", "rm ", "format ", "fdisk ",
    "shutdown", "restart", "reboot", "halt", "poweroff",
    " > ", " >> ", " | ", "sudo ", "runas ", "net user", "net localgroup",
    "reg add", "reg delete", "reg copy", "reg save", "reg load",
    "sc delete", "sc stop", "sc config",
]

def _is_safe_command(cmd: str):
    """Determine whether a command is safe. Returns (is_safe, reason)."""
    if not cmd or not cmd.strip():
        return False, "empty"
    cmd_lower = cmd.strip().lower()
    first_word = cmd_lower.split()[0] if cmd_lower.split() else ""

    # Check dangerous keywords first
    for kw in DANGEROUS_KEYWORDS:
        if kw in cmd_lower:
            return False, "dangerous"

    # Whitelist commands
    if first_word in SAFE_COMMANDS:
        return True, "safe"

    # Unknown commands: default to safe, execute directly (reduce user disturbance)
    return True, "unknown"


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on", "open"):
            return True
        if v in ("0", "false", "no", "off", "close"):
            return False
        return bool(value)
    return bool(value)


def _to_number(value, default):
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            v = value.strip()
            if v.lower() in ("", "none", "null"):
                return default
            return float(v) if "." in v else int(v)
    except (TypeError, ValueError):
        pass
    return default


@neko_plugin
class SandboxToolPlugin(NekoPluginBase):
    """Sandbox tool: read/write arbitrary files, execute commands (safe commands run directly, dangerous commands require confirmation), operation logging persisted to disk."""

    # ---------- Lifecycle ----------
    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self.logger.info("SandboxTool started")
        self._pending = {}
        try:
            pending_path = self.data_path(PENDING_FILE)
            if pending_path.exists():
                with open(pending_path, "r", encoding="utf-8") as f:
                    self._pending = json.load(f)
        except Exception as e:
            self.logger.warning(f"Failed to restore pending commands: {e}")
        self._ensure_log_dir()
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self.logger.info("SandboxTool shutdown")
        try:
            pending_path = self.data_path(PENDING_FILE)
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            with open(pending_path, "w", encoding="utf-8") as f:
                json.dump(self._pending, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"Failed to save pending commands: {e}")
        return Ok({"status": "stopped"})

    # ---------- Configuration ----------
    def _settings_path(self):
        return self.data_path("settings.json")

    def _load_settings(self):
        try:
            path = self._settings_path()
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    settings = dict(DEFAULTS)
                    settings.update({k: v for k, v in data.items() if k in DEFAULTS})
                    return settings
        except Exception as e:
            self.logger.warning(f"Failed to read configuration, using defaults: {e}")
        return dict(DEFAULTS)

    def _save_settings(self, settings):
        try:
            path = self._settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save configuration: {e}")

    # ---------- Logging ----------
    def _log_path(self):
        cfg = self._load_settings()
        lp = cfg.get("log_path", "logs/sandbox_tool.log")
        p = Path(lp)
        if not p.is_absolute():
            p = self.data_path(lp)
        return p

    def _ensure_log_dir(self):
        try:
            log_path = self._log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _append_log(self, op_type: str, target: str, result: str, confirmed: Optional[bool] = None):
        """Append one operation to the log file."""
        try:
            self._ensure_log_dir()
            log_path = self._log_path()
            cfg = self._load_settings()
            max_lines = _to_number(cfg.get("max_log_lines"), 10000)

            entry = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "op": op_type,
                "target": target,
                "result": result,
            }
            if confirmed is not None:
                entry["confirmed"] = confirmed

            line = json.dumps(entry, ensure_ascii=False)

            lines = []
            if log_path.exists():
                try:
                    with open(log_path, "r", encoding="utf-8") as f:
                        lines = [ln.rstrip("\n") for ln in f.readlines() if ln.strip()]
                except Exception:
                    lines = []
            lines.append(line)
            if len(lines) > max_lines:
                lines = lines[-max_lines:]

            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            try:
                self.logger.warning(f"Failed to write log: {e}")
            except Exception:
                pass

    # ---------- a_readfile: read arbitrary file ----------
    @llm_tool(
        name="read_file",
        description="Read arbitrary file content (all files, no path excluded).",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, supports absolute and relative paths.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Limit read length, default 2MB (2000000).",
                    "default": 2000000,
                    "minimum": 1,
                },
            },
            "required": ["path"],
        },
        role=None,
    )
    @plugin_entry(
        id="a_readfile",
        name="Read File",
        description="Read arbitrary file content (all files, no path excluded).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, supports absolute and relative paths.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Limit read length, default 2MB (2000000).",
                    "default": 2000000,
                    "minimum": 1,
                },
            },
            "required": ["path"],
        },
    )
    async def read_file(self, *, path: str, max_bytes: "str | None" = None, _ctx=None):
        """Read arbitrary file, return file content as text."""
        try:
            p = Path(path)
            if not p.exists():
                result = f"File not found: {path}"
                self._append_log("read", str(path), result)
                return Ok({"ok": False, "result": result})
            if not p.is_file():
                result = f"Not a regular file: {path}"
                self._append_log("read", str(path), result)
                return Ok({"ok": False, "result": result})

            mb = _to_number(max_bytes, 2_000_000)
            data = p.read_bytes()[:mb]
            text = None
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    text = data.decode(enc)
                    break
                except Exception:
                    continue
            if text is None:
                text = data.decode("utf-8", errors="replace")

            result = f"--- File: {path} ---\n{text}"
            self._append_log("read", str(path), f"ok, bytes={len(data)}")
            return Ok({"ok": True, "result": result, "bytes": len(data)})
        except Exception as e:
            result = f"Read failed: {e}"
            self._append_log("read", str(path), result)
            return Ok({"ok": False, "result": result})

    # ---------- b_writefile: write arbitrary file ----------
    @llm_tool(
        name="write_file",
        description="Write arbitrary file (all files, no path excluded).",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, supports absolute and relative paths.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write.",
                },
                "create_dirs": {
                    "type": "boolean",
                    "description": "Whether to automatically create parent directories, default true.",
                    "default": True,
                },
            },
            "required": ["path", "content"],
        },
        role=None,
    )
    @plugin_entry(
        id="b_writefile",
        name="Write File",
        description="Write arbitrary file (all files, no path excluded).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path, supports absolute and relative paths.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write.",
                },
                "create_dirs": {
                    "type": "boolean",
                    "description": "Whether to automatically create parent directories, default true.",
                    "default": True,
                },
            },
            "required": ["path", "content"],
        },
    )
    async def write_file(self, *, path: str, content: str, create_dirs: "str | None" = "true", _ctx=None):
        """Write arbitrary file, automatically create parent directories."""
        try:
            p = Path(path)
            if _to_bool(create_dirs):
                p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            result = f"Written: {path} ({len(content.encode('utf-8'))} bytes)"
            self._append_log("write", str(path), result)
            return Ok({"ok": True, "result": result, "bytes": len(content.encode("utf-8"))})
        except Exception as e:
            result = f"Write failed: {e}"
            self._append_log("write", str(path), result)
            return Ok({"ok": False, "result": result})

    # ---------- c_listdir: list directory ----------
    @llm_tool(
        name="list_dir",
        description="List files and subdirectories under any directory (all paths, no exclusion).",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path, supports absolute and relative paths.",
                },
            },
            "required": ["path"],
        },
        role=None,
    )
    @plugin_entry(
        id="c_listdir",
        name="List Directory",
        description="List files and subdirectories under any directory (all paths, no exclusion).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path, supports absolute and relative paths.",
                },
            },
            "required": ["path"],
        },
    )
    async def list_dir(self, *, path: str, _ctx=None):
        """List directory contents, return entry names, types, sizes."""
        try:
            p = Path(path)
            if not p.exists():
                result = f"Directory not found: {path}"
                self._append_log("listdir", str(path), result)
                return Ok({"ok": False, "result": result})
            if not p.is_dir():
                result = f"Not a directory: {path}"
                self._append_log("listdir", str(path), result)
                return Ok({"ok": False, "result": result})

            entries = []
            for child in p.iterdir():
                try:
                    st = child.stat()
                    entries.append({
                        "name": child.name,
                        "type": "dir" if child.is_dir() else "file",
                        "size": st.st_size,
                        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                    })
                except Exception:
                    entries.append({"name": child.name, "type": "unknown", "size": 0, "mtime": ""})
            entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))

            result_lines = [f"--- Directory: {path} ({len(entries)} entries) ---"]
            for e in entries:
                result_lines.append(f"[{e['type']}] {e['name']}  ({e['size']} bytes, {e['mtime']})")
            result = "\n".join(result_lines)
            self._append_log("listdir", str(path), f"ok, {len(entries)} entries")

            return Ok({"ok": True, "result": result, "entries": entries})
        except Exception as e:
            result = f"Failed to list directory: {e}"
            self._append_log("listdir", str(path), result)
            return Ok({"ok": False, "result": result})

    # ---------- d_command: submit command ----------
    @llm_tool(
        name="run_command",
        description="Submit a command for execution; whitelist commands run directly, dangerous commands return pending confirmation, requiring confirm_command agreement before execution.",
        parameters={
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "Command string to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Command timeout in seconds, default 30.",
                    "default": 30,
                    "minimum": 1,
                },
            },
            "required": ["cmd"],
        },
        role=None,
    )
    @plugin_entry(
        id="d_command",
        name="Submit Command",
        description="Submit a command. Safe commands run directly; dangerous commands push_message and wait for user confirmation.",
        input_schema={
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "Command string to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Command timeout in seconds, default 30.",
                    "default": 30,
                    "minimum": 1,
                },
            },
            "required": ["cmd"],
        },
    )
    async def submit_command(self, *, cmd: str, timeout: "str | None" = None, _ctx=None):
        """Submit a command. Safe commands run directly; dangerous commands wait for user confirmation."""
        cfg = self._load_settings()
        command_timeout = _to_number(timeout, cfg.get("command_timeout", 30))
        confirm_required = _to_bool(cfg.get("confirm_required", True))
        auto_execute_safe = _to_bool(cfg.get("auto_execute_safe", True))

        command_id = str(uuid.uuid4())[:8]
        self._pending[command_id] = {
            "command_id": command_id,
            "cmd": cmd,
            "timeout": command_timeout,
            "created_at": time.time(),
            "confirmed": False,
            "executed": False,
            "result": None,
        }

        # Persist pending
        try:
            pending_path = self.data_path(PENDING_FILE)
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            with open(pending_path, "w", encoding="utf-8") as f:
                json.dump(self._pending, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"Failed to save pending command: {e}")

        is_safe, reason = _is_safe_command(cmd)

        # Safe command: execute directly, no confirmation needed
        if is_safe and auto_execute_safe:
            return await self._execute_command(command_id)

        # Dangerous command: requires confirmation
        if confirm_required:
            text = (
                f"[WARNING] Sandbox tool dangerous command pending confirmation\n"
                f"Command ID: {command_id}\n"
                f"Command: {cmd}\n"
                f"Risk: {reason}\n"
                f"Timeout: {command_timeout} seconds\n\n"
                f"If you agree to execute, please use e_confirm entry with command_id={command_id}."
            )
            self.push_message(
                source="sandbox_tool",
                visibility=["chat"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": text}],
                priority=5,
            )
            self._append_log("command_submit", cmd, f"pending, id={command_id}, reason={reason}", confirmed=False)
            return Ok({
                "ok": True,
                "status": "pending_confirm",
                "command_id": command_id,
                "msg": f"Command submitted ({reason}), waiting for user confirmation before execution. Use e_confirm to confirm.",
            })
        else:
            # Confirmation mechanism disabled, execute directly
            return await self._execute_command(command_id)

    # ---------- e_confirm: confirm command execution ----------
    @llm_tool(
        name="confirm_command",
        description="Confirm or reject a pending command.",
        parameters={
            "type": "object",
            "properties": {
                "command_id": {
                    "type": "string",
                    "description": "Command ID returned by run_command.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "true means agree to execute, false means reject, default true.",
                    "default": True,
                },
            },
            "required": ["command_id"],
        },
        role=None,
    )
    @plugin_entry(
        id="e_confirm",
        name="Confirm Command Execution",
        description="Confirm or reject execution of a pending command.",
        input_schema={
            "type": "object",
            "properties": {
                "command_id": {
                    "type": "string",
                    "description": "Command ID returned by d_command.",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "true means agree to execute, false means reject, default true.",
                    "default": True,
                },
            },
            "required": ["command_id"],
        },
    )
    async def confirm_command(self, *, command_id: str, confirm: "str | None" = "true", _ctx=None):
        """User confirms or rejects command execution."""
        if command_id not in self._pending:
            result = f"Command ID not found: {command_id}"
            self._append_log("command_confirm", command_id, result)
            return Ok({"ok": False, "result": result})

        pending = self._pending[command_id]
        if pending.get("executed"):
            result = f"Command {command_id} has already been executed, result: {pending.get('result')}"
            return Ok({"ok": True, "result": result, "executed": True})

        if not _to_bool(confirm):
            pending["confirmed"] = False
            pending["executed"] = True
            pending["result"] = "User rejected execution"
            self._append_log("command_confirm", pending["cmd"], "User rejected execution", confirmed=False)
            return Ok({"ok": True, "result": "Command execution rejected", "command_id": command_id, "executed": False})

        # Confirm execution
        return await self._execute_command(command_id)

    async def _execute_command(self, command_id: str):
        """Actually execute a command (subprocess with timeout)."""
        pending = self._pending[command_id]
        cmd = pending["cmd"]
        timeout = pending["timeout"]

        try:
            completed = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                timeout=timeout,
            )
            raw_stdout = completed.stdout or b""
            raw_stderr = completed.stderr or b""

            def _decode_windows(raw: bytes):
                # Windows cmd.exe output encoding depends on system active code page (chcp)
                # utf-8 -> mbcs(Windows ANSI) -> gbk -> fallback
                for enc in ("utf-8", "mbcs", "gbk"):
                    try:
                        return raw.decode(enc)
                    except UnicodeDecodeError:
                        continue
                return raw.decode("utf-8", errors="replace")

            stdout = _decode_windows(raw_stdout)
            stderr = _decode_windows(raw_stderr)
            result_text = (
                f"Exit code: {completed.returncode}\n"
                f"--- Standard output ---\n{stdout}\n"
                f"--- Standard error ---\n{stderr}"
            )
            pending["executed"] = True
            pending["confirmed"] = True
            pending["result"] = result_text[:5000]
            self._append_log("command_exec", cmd, f"exit={completed.returncode}, bytes_out={len(stdout)}, bytes_err={len(stderr)}", confirmed=True)

            try:
                pending_path = self.data_path(PENDING_FILE)
                with open(pending_path, "w", encoding="utf-8") as f:
                    json.dump(self._pending, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            return Ok({
                "ok": True,
                "command_id": command_id,
                "executed": True,
                "returncode": completed.returncode,
                "result": result_text,
            })
        except subprocess.TimeoutExpired:
            pending["executed"] = True
            pending["confirmed"] = True
            pending["result"] = f"Command execution timed out (> {timeout} seconds)"
            self._append_log("command_exec", cmd, f"timeout>{timeout}s", confirmed=True)
            return Ok({
                "ok": False,
                "command_id": command_id,
                "executed": True,
                "returncode": -1,
                "result": f"Command execution timed out (> {timeout} seconds)",
            })
        except Exception as e:
            pending["executed"] = True
            pending["confirmed"] = True
            pending["result"] = f"Execution exception: {e}"
            self._append_log("command_exec", cmd, f"exception: {e}", confirmed=True)
            return Ok({
                "ok": False,
                "command_id": command_id,
                "executed": True,
                "returncode": -1,
                "result": f"Execution exception: {e}",
            })

    # ---------- f_logs: view operation log ----------
    @llm_tool(
        name="view_logs",
        description="View sandbox tool operation log.",
        parameters={
            "type": "object",
            "properties": {
                "tail": {
                    "type": "integer",
                    "description": "Number of last lines to show, default 100.",
                    "default": 100,
                    "minimum": 1,
                },
            },
            "required": [],
        },
        role=None,
    )
    @plugin_entry(
        id="f_logs",
        name="View Logs",
        description="View operation logs.",
        input_schema={
            "type": "object",
            "properties": {
                "tail": {
                    "type": "integer",
                    "description": "Number of last lines to show, default 100.",
                    "default": 100,
                    "minimum": 1,
                },
            },
            "required": [],
        },
    )
    async def view_logs(self, *, tail: "str | None" = "100", _ctx=None):
        """View operation log file, return last tail lines."""
        try:
            log_path = self._log_path()
            n = _to_number(tail, 100)
            if not log_path.exists():
                result = "Log file not found, no operation records yet."
                return Ok({"ok": True, "result": result, "lines": []})

            with open(log_path, "r", encoding="utf-8") as f:
                all_lines = [ln.rstrip("\n") for ln in f.readlines() if ln.strip()]

            show = all_lines[-n:] if n > 0 else all_lines
            result = "\n".join(show) if show else "(empty)"
            return Ok({"ok": True, "result": result, "lines": show, "total": len(all_lines)})
        except Exception as e:
            result = f"Failed to read log: {e}"
            return Ok({"ok": False, "result": result})
