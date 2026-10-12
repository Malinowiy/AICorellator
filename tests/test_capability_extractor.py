"""Unit tests for the capability extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator.parsing.capability_extractor import extract


def _write(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return path


def test_empty_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.py", "")
    result = extract(path, "empty.py")
    assert result.symbols == []
    assert result.module_capabilities == set()


def test_subprocess_shell(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import subprocess

def dangerous(cmd):
    subprocess.run(cmd, shell=True)
""")
    result = extract(path, "a.py")
    assert len(result.symbols) == 1
    caps = result.symbols[0].capabilities
    assert "executes_code" in caps
    assert "executes_shell" in caps


def test_subprocess_no_shell(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import subprocess

def safe(cmd):
    subprocess.run(cmd)
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "executes_code" in caps
    assert "executes_shell" not in caps


def test_os_system(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import os

def dangerous(cmd):
    os.system(cmd)
""")
    result = extract(path, "a.py")
    assert "executes_shell" in result.symbols[0].capabilities


def test_file_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def read_it(p):
    with open(p, "r") as f:
        return f.read()
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "reads_filesystem" in caps
    assert "writes_filesystem" not in caps


def test_file_write(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def write_it(p):
    with open(p, "w") as f:
        f.write("x")
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "writes_filesystem" in caps


def test_http_egress(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import requests

def send(url):
    return requests.post(url, data="x")
""")
    result = extract(path, "a.py")
    assert "http_egress" in result.symbols[0].capabilities


def test_reads_env(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import os

def get_config():
    return os.getenv("DATABASE_URL")
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "reads_env" in caps
    assert "reads_secrets" not in caps  # DATABASE_URL is not a secret


def test_reads_secrets(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import os

def get_key():
    return os.getenv("OPENAI_API_KEY")
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "reads_env" in caps
    assert "reads_secrets" in caps


def test_sql_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def fetch(cursor):
    cursor.execute("SELECT * FROM users")
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "reads_db" in caps
    assert "writes_db" not in caps


def test_sql_write(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def store(cursor):
    cursor.execute("INSERT INTO users VALUES (1)")
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "writes_db" in caps


def test_calls_llm(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import openai

def chat(prompt):
    return openai.chat.completions.create(model="gpt", messages=[])
""")
    result = extract(path, "a.py")
    assert "calls_llm" in result.symbols[0].capabilities


def test_class_capabilities_aggregated(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
import subprocess
import requests

class MyAgent:
    def run_shell(self, cmd):
        subprocess.run(cmd, shell=True)

    def call_home(self, url):
        requests.post(url)
""")
    result = extract(path, "a.py")
    my_agent = next(
        (s for s in result.symbols if s.symbol_name == "MyAgent"),
        None,
    )
    assert my_agent is not None
    assert "executes_shell" in my_agent.capabilities
    assert "http_egress" in my_agent.capabilities


def test_syntax_error_handled(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken.py", "this is ( not : valid")
    result = extract(path, "broken.py")
    assert result.symbols == []

def test_nested_function_capabilities(tmp_path: Path) -> None:
    """Functions defined inside factories are found by name."""
    path = _write(tmp_path, "a.py", """
import subprocess

def create_server():
    @mcp.tool
    def execute_script(script):
        subprocess.run(script, shell=True)
    return execute_script
""")
    result = extract(path, "a.py")
    names = {s.symbol_name for s in result.symbols}
    assert "create_server" in names
    assert "execute_script" in names

    execute = next(s for s in result.symbols if s.symbol_name == "execute_script")
    assert "executes_shell" in execute.capabilities

def test_class_before_methods(tmp_path: Path) -> None:
    """Class symbol precedes its methods in the output."""
    path = _write(tmp_path, "a.py", """
import subprocess

class MyAgent:
    def run_shell(self, cmd):
        subprocess.run(cmd, shell=True)
""")
    result = extract(path, "a.py")
    names = [s.symbol_name for s in result.symbols]
    assert names[0] == "MyAgent"
    assert names[1] == "run_shell"