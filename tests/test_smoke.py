"""Smoke tests for sandbox_tool plugin."""

import asyncio

from plugin.plugins.sandbox_tool import SandboxToolPlugin


def test_plugin_id():
    assert SandboxToolPlugin is not None


def test_read_file(tmp_path):
    p = tmp_path / "hello.txt"
    p.write_text("hello world", encoding="utf-8")
    plugin = SandboxToolPlugin.__new__(SandboxToolPlugin)
    result = asyncio.run(plugin.read_file(path=str(p), max_bytes=1000))
    assert result.is_ok() is True
    assert "hello world" in result.value.get("result", "")


def test_write_file(tmp_path):
    p = tmp_path / "subdir" / "hello.txt"
    plugin = SandboxToolPlugin.__new__(SandboxToolPlugin)
    result = asyncio.run(plugin.write_file(path=str(p), content="hi there", create_dirs=True))
    assert result.is_ok() is True
    assert p.read_text(encoding="utf-8") == "hi there"


def test_list_dir(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    plugin = SandboxToolPlugin.__new__(SandboxToolPlugin)
    result = asyncio.run(plugin.list_dir(path=str(tmp_path)))
    assert result.is_ok() is True
    names = [e["name"] for e in result.value.get("entries", [])]
    assert "a.txt" in names
    assert "b.txt" in names