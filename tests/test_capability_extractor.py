"""Unit tests for the capability extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator import patterns
from aicorellator.parsing.capability_extractor import _matches_any, extract
from aicorellator.patterns import (
    CAPABILITY_ORM_READS_DB,
    CAPABILITY_ORM_WRITES_DB,
    CAPABILITY_READS_DB,
    CAPABILITY_WRITES_DB,
)


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


# --- ORM call sites (1.2a.3) ---
# Session and repository methods carry no SQL text, so the method
# name itself is the evidence. Patterns are two-segment
# (`db.delete`), which matches `self.db.delete` but not `self.delete`.

def test_orm_db_delete_is_write(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def delete_file(self, f):
    self.db.delete(f)
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "writes_db" in caps


def test_orm_db_commit_is_write(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def save(self):
    self.db.commit()
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "writes_db" in caps


def test_orm_session_add_is_write(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def store(obj):
    session.add(obj)
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "writes_db" in caps


def test_orm_db_query_is_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def find(self, name):
    return self.db.query(name)
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "reads_db" in caps
    assert "writes_db" not in caps


def test_orm_session_query_is_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def all_users():
    return session.query(User).all()
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "reads_db" in caps


def test_orm_repository_get_is_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def load(self, id):
    return self.repository.get(id)
""")
    result = extract(path, "a.py")
    caps = result.symbols[0].capabilities
    assert "reads_db" in caps


def test_bare_delete_is_not_a_write(tmp_path: Path) -> None:
    """`self.delete` has no `db`/`session` qualifier — not a DB write."""
    path = _write(tmp_path, "a.py", """
def purge(self, f):
    self.delete(f)
""")
    result = extract(path, "a.py")
    # A symbol with no capabilities is not recorded at all.
    assert all(
        "writes_db" not in s.capabilities for s in result.symbols
    )


def test_module_level_db_delete_is_write(tmp_path: Path) -> None:
    """The matcher is not tied to `self.` — a module-level `db` works."""
    path = _write(tmp_path, "a.py", """
def purge(f):
    db.delete(f)
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


def test_orm_patterns_are_registered() -> None:
    """The ORM names are wired into both direction registries."""
    assert "db.delete" in CAPABILITY_WRITES_DB
    assert "repo.update" in CAPABILITY_WRITES_DB
    assert "db.query" in CAPABILITY_READS_DB


# --- Verb families (1.2a.3b) ---
# Repository APIs name methods `<verb>_<noun>` (`repo.get_file`), so the
# exact two-segment patterns miss them. A pattern may end in `*`: the
# qualifier must still match exactly, the final segment only has to
# start with the verb. The `*` form is confined to the ORM lists.

def test_matches_any_verb_family_matches_prefixed_segments() -> None:
    """`repo.get*` covers the bare verb, `_noun`, and `_all_nouns`."""
    for name in (
        "repo.get",
        "repo.get_file",
        "repo.get_file_by_id",
        "repo.get_all_files",
    ):
        assert _matches_any(name, ("repo.get*",)), name


def test_matches_any_verb_family_matches_dotted_prefix() -> None:
    """The qualifier may sit behind `self.` like any other pattern."""
    assert _matches_any("self.repo.get_file", ("repo.get*",))
    assert _matches_any("self.db.get_user", ("db.get*",))
    assert _matches_any("session.repo.get_file", ("repo.get*",))


def test_matches_any_verb_family_requires_exact_qualifier() -> None:
    """A different object name is not a repository call."""
    assert not _matches_any("notrepo.get_file", ("repo.get*",))
    assert not _matches_any("mydb.get_user", ("db.get*",))
    assert not _matches_any("get_file", ("repo.get*",))


def test_matches_any_verb_family_delete_and_plural_nouns() -> None:
    assert _matches_any("repo.delete_file", ("repo.delete*",))
    assert _matches_any("repo.delete_transactions", ("repo.delete*",))
    assert not _matches_any("repo.delete_file", ("repo.create*",))


def test_matches_any_exact_patterns_unchanged_by_wildcards() -> None:
    """A pattern without `*` keeps its old trailing-segment semantics."""
    assert _matches_any("self.db.commit", ("db.commit",))
    assert _matches_any("repo.db.commit", ("db.commit",))
    assert _matches_any("session.db.delete", ("db.delete",))
    assert not _matches_any("self.commit", ("db.commit",))


def test_matches_any_mid_star_is_not_supported() -> None:
    """Only a *trailing* star is a wildcard; `repo.*_file` is literal.

    Documented limitation: a mid-pattern star would need a regex, and
    the ORM verb families do not require one (1.2a.3b).
    """
    assert not _matches_any("repo.get_file", ("repo.*_file",))


def test_wildcards_are_confined_to_the_orm_lists() -> None:
    """Every `*` pattern anywhere in the registry is an ORM verb family."""
    allowed = set(CAPABILITY_ORM_READS_DB) | set(CAPABILITY_ORM_WRITES_DB)
    for name, value in vars(patterns).items():
        if not name.startswith("CAPABILITY_") or not isinstance(
            value, (tuple, frozenset)
        ):
            continue
        for pattern in value:
            if "*" in pattern:
                assert pattern in allowed, f"{name} has a stray wildcard: {pattern}"


def test_orm_wildcards_are_exactly_the_verb_families() -> None:
    """The ORM lists carry no hand-added wildcard and no direction overlap."""
    expected = {
        f"{qualifier}.{verb}*"
        for qualifier in ("db", "session", "repository", "repo")
        for verb in (
            "get", "find", "list", "filter", "all",
            "add", "delete", "save", "create", "update",
        )
    }
    reads = {p for p in CAPABILITY_ORM_READS_DB if "*" in p}
    writes = {p for p in CAPABILITY_ORM_WRITES_DB if "*" in p}
    assert reads | writes == expected
    assert not reads & writes


def test_orm_verb_families_are_registered() -> None:
    """The verb families reach both direction registries."""
    assert "repo.get*" in CAPABILITY_READS_DB
    assert "db.get*" in CAPABILITY_READS_DB
    assert "repo.delete*" in CAPABILITY_WRITES_DB
    assert "session.create*" in CAPABILITY_WRITES_DB


def test_orm_read_verb_families_cover_every_qualifier() -> None:
    """db / session / repository / repo × the five read verbs."""
    for qualifier in ("db", "session", "repository", "repo"):
        for verb in ("get", "find", "list", "filter", "all"):
            assert f"{qualifier}.{verb}*" in CAPABILITY_READS_DB


def test_orm_write_verb_families_cover_every_qualifier() -> None:
    """db / session / repository / repo × the five write verbs."""
    for qualifier in ("db", "session", "repository", "repo"):
        for verb in ("add", "delete", "save", "create", "update"):
            assert f"{qualifier}.{verb}*" in CAPABILITY_WRITES_DB


def test_repo_get_file_is_a_read(tmp_path: Path) -> None:
    """FinBot's `repo.get_file(file_id)` — a repository read."""
    path = _write(tmp_path, "a.py", """
def download(self, file_id):
    return repo.get_file(file_id)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "reads_db" in caps
    assert "writes_db" not in caps


def test_repo_delete_file_is_a_write(tmp_path: Path) -> None:
    """FinBot's `repo.delete_file(f)` — a repository write."""
    path = _write(tmp_path, "a.py", """
def remove(self, f):
    repo.delete_file(f)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "writes_db" in caps


def test_db_get_user_is_a_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def whoami(self, id):
    return self.db.get_user(id)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "reads_db" in caps


def test_session_delete_transaction_is_a_write(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def rollback(t):
    session.delete_transaction(t)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "writes_db" in caps


def test_bare_delete_file_is_not_a_write(tmp_path: Path) -> None:
    """`self.delete_file` has no repository qualifier — not a DB write."""
    path = _write(tmp_path, "a.py", """
def purge(self, f):
    self.delete_file(f)
""")
    result = extract(path, "a.py")
    assert all(
        "writes_db" not in s.capabilities for s in result.symbols
    )


def test_repository_find_vendors_is_a_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def vendors(self):
    return repository.find_vendors()
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "reads_db" in caps
    assert "session.query" in CAPABILITY_READS_DB