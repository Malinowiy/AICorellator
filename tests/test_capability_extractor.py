"""Unit tests for the capability extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from aicorellator import patterns
from aicorellator.parsing.capability_extractor import (
    _credit,
    _matches_any,
    extract,
)
# The one end-to-end fixture (B.10a) reads the extractor's capabilities
# and hands them to `classify_class`, because "the class got no kind" is
# the consequence the capability gap had. Everything else about
# classification stays in `test_classification.py`.
from aicorellator.parsing.classification import classify_class
from aicorellator.patterns import (
    CAPABILITY_DB_STATEMENT_CALLS,
    CAPABILITY_ORM_READS_DB,
    CAPABILITY_ORM_WRITES_DB,
    CAPABILITY_READS_DB,
    CAPABILITY_WRITES_DB,
    ORM_COMMIT_CALLS,
)
from aicorellator.patterns_registry import ROUTING_CAPABILITIES


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


# --- Leading-underscore qualifiers (1.2a.3e) ---
# A private attribute is the same object as a public one, so the
# matcher ignores leading underscores on the receiver: `_db` matches
# `db`, `_conn` matches `conn`, `_cur` matches `cur`. mcploitable
# writes `_conn.commit()` and `cur.execute(...)` throughout and was
# invisible to the ORM lists before this. The verb is never
# normalized — `db._delete` is a private method, not the public ORM
# `delete`.

def test_private_db_qualifier_delete_is_a_write(tmp_path: Path) -> None:
    """`self._db.delete(f)` is `self.db.delete(f)` on a private session."""
    path = _write(tmp_path, "a.py", """
def purge(self, f):
    self._db.delete(f)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "writes_db" in caps


def test_private_session_qualifier_commit_is_a_write(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def save(self):
    self._session.commit()
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "writes_db" in caps


def test_double_underscore_qualifier_is_normalized(tmp_path: Path) -> None:
    """`self.__db` is class-private: name mangling rewrites it to
    `_Class__db` at runtime, but the AST holds the source spelling, so
    the double underscore normalizes to `db` like any other."""
    path = _write(tmp_path, "a.py", """
def purge(self, f):
    self.__db.delete(f)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "writes_db" in caps


def test_private_repo_qualifier_get_is_a_read(tmp_path: Path) -> None:
    path = _write(tmp_path, "a.py", """
def load(self, id):
    return self._repo.get(id)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "reads_db" in caps


def test_private_verb_is_not_normalized(tmp_path: Path) -> None:
    """`db._delete` is a private method, not the public ORM `delete`."""
    path = _write(tmp_path, "a.py", """
def purge(self, f):
    self.db._delete(f)
""")
    result = extract(path, "a.py")
    assert all(
        "writes_db" not in s.capabilities for s in result.symbols
    )


def test_underscore_normalization_preserves_exact_matching() -> None:
    """The new rule is additive: nothing that matched before stops."""
    assert _matches_any("self.db.delete", ("db.delete",))
    assert _matches_any("self.db.get_user", ("db.get*",))
    assert not _matches_any("notrepo.get_file", ("repo.get*",))
    # Underscores are stripped, not ignored: a different name stays
    # a different name.
    assert not _matches_any("_notdb.get_file", ("db.get*",))
    assert not _matches_any("_myrepository.get", ("repository.get",))


def test_private_conn_commit_is_a_commit_but_not_a_write() -> None:
    """1.2a.3c kept `conn.commit` out of the write capabilities; the
    underscore normalization reaches the Flow Analyzer's commit list
    without changing that decision (1.2a.3e)."""
    assert _matches_any("self._conn.commit", ORM_COMMIT_CALLS)
    assert not _matches_any("self._conn.commit", CAPABILITY_WRITES_DB)


# --- Cursor abbreviation (1.2a.3e) ---

def test_matches_any_cursor_abbreviation() -> None:
    """`cur` is the usual abbreviation for a cursor object; the verb
    that follows decides how the statement is classified."""
    assert _matches_any("cur.execute", CAPABILITY_DB_STATEMENT_CALLS)
    assert _matches_any("cur.executemany", CAPABILITY_DB_STATEMENT_CALLS)
    # The receiver may be private, like any other qualifier.
    assert _matches_any("_cur.execute", CAPABILITY_DB_STATEMENT_CALLS)
    assert _matches_any("_cursor.execute", CAPABILITY_DB_STATEMENT_CALLS)
    # Only the two statement shapes — `cur.rowcount` is an attribute,
    # and a different name is still a different name. (`cur.fetchall`
    # is a read since 1.2a.3h, but it lives in CAPABILITY_ORM_READS_DB:
    # a fetch carries no SQL string for the keyword branch to inspect.)
    assert not _matches_any("cur.fetchall", CAPABILITY_DB_STATEMENT_CALLS)
    assert not _matches_any("mycur.execute", CAPABILITY_DB_STATEMENT_CALLS)


def test_private_cursor_execute_with_sql_keyword_is_a_read(
    tmp_path: Path,
) -> None:
    """mcploitable's `cur = _conn.cursor(); cur.execute("SELECT ...")`."""
    path = _write(tmp_path, "a.py", """
def count(self, ticket_id):
    _cur.execute("SELECT COUNT(*) FROM support_tickets")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "reads_db" in caps


def test_cursor_executemany_with_sql_keyword_is_a_write(
    tmp_path: Path,
) -> None:
    """mcploitable's seeding shape: `cur.executemany("INSERT ...", rows)`."""
    path = _write(tmp_path, "a.py", """
def seed(self, rows):
    cur.executemany("INSERT INTO tokens (service, token) VALUES (?, ?)", rows)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "writes_db" in caps


# --- Cursor fetch reads (1.2a.3h) ---
# A fetch is the read half of a statement call: `cur.execute("SELECT ...")`
# runs the query, `cur.fetchone()` collects the rows. It cannot be a
# statement call — there is no SQL string on the fetch to inspect, the
# query was executed earlier — so the method name is the evidence and the
# patterns live in CAPABILITY_ORM_READS_DB.

def test_cursor_fetch_calls_are_reads(tmp_path: Path) -> None:
    """Every cursor fetch shape gives `reads_db` and nothing else."""
    for call in (
        "cur.fetchone()",
        "cur.fetchall()",
        "cur.fetchmany(10)",
        "cursor.fetchall()",
        "self.cursor.fetchone()",
    ):
        path = _write(tmp_path, "a.py", f"""
def read_rows(self):
    {call}
""")
        caps = extract(path, "a.py").symbols[0].capabilities
        assert "reads_db" in caps, call
        assert "writes_db" not in caps, call


def test_private_cursor_fetch_is_a_read(tmp_path: Path) -> None:
    """`_cur` / `_cursor` are cursors too — the 1.2a.3e qualifier rule
    reaches the fetch patterns without a matcher change."""
    path = _write(tmp_path, "a.py", """
def read_rows(self):
    _cur.fetchone()
    _cursor.fetchall()
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "reads_db" in caps


def test_fetch_on_a_non_cursor_receiver_is_not_a_read(
    tmp_path: Path,
) -> None:
    """A row-like object is not a cursor: the qualifier is the evidence.
    `open(...)` is the anchor — it keeps the symbol in the result so the
    assertion is not an empty-set pass."""
    path = _write(tmp_path, "a.py", """
def read_rows(self, row, queue):
    row.fetchone()
    queue.fetchall()
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_fetch_next_to_a_write_gives_both_capabilities(
    tmp_path: Path,
) -> None:
    """The real effect: a sink that writes and reads back its rows now
    carries `writes_db` **and** `reads_db` — the prompt can weigh both."""
    path = _write(tmp_path, "a.py", """
def insert_and_read(self, rows):
    cursor.execute("INSERT INTO t VALUES (1)")
    cursor.executemany("INSERT INTO t VALUES (?)", rows)
    return cursor.fetchall()
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert "writes_db" in caps
    assert "reads_db" in caps


def test_matches_any_cursor_fetch_patterns() -> None:
    """The fetch patterns are reads, not statement calls (1.2a.3h)."""
    for target in (
        "cur.fetchone", "cur.fetchall", "cur.fetchmany",
        "cursor.fetchone", "cursor.fetchall", "cursor.fetchmany",
        "_cur.fetchone", "self.cursor.fetchall",
    ):
        assert _matches_any(target, CAPABILITY_ORM_READS_DB), target
        # Unchanged: a fetch is not a statement call.
        assert not _matches_any(target, CAPABILITY_DB_STATEMENT_CALLS), target
    # A different receiver is still a different receiver, and
    # `cursor.description` / `cursor.rowcount` are metadata, not reads.
    assert not _matches_any("row.fetchone", CAPABILITY_ORM_READS_DB)
    assert not _matches_any("mycur.fetchall", CAPABILITY_ORM_READS_DB)
    assert not _matches_any("cursor.description", CAPABILITY_ORM_READS_DB)
    assert not _matches_any("cursor.rowcount", CAPABILITY_ORM_READS_DB)


# --- Adapter attribute patterns (1.2a.3i) ---
# A call through a wrapper object has no module name to match:
# `self.adapter.execute(...)`, `self.client.post(...)`,
# `self.llm.invoke(...)` are methods on a variable. The pattern therefore
# names a qualifier *plus* verb (`adapter.execute`) and the
# trailing-segment rule does the rest, so `self.adapter.execute` and
# `self.vendor.adapter.execute` both match. The qualifier is required:
# a bare `execute()`, a bare `post()`, and a `self.<verb>()` call match
# nothing. The patterns live in CAPABILITY_EXECUTES_CODE,
# CAPABILITY_HTTP_EGRESS and CAPABILITY_CALLS_LLM; the database lists
# already cover their adapter shapes (1.2a.3b) and were not touched.

def test_shell_adapter_run_is_executes_code(tmp_path: Path) -> None:
    """`self.shell.run("ls")` is a shell wrapper, not a module call."""
    path = _write(tmp_path, "a.py", """
def run_command(self):
    self.shell.run("ls")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"executes_code"}


def test_adapter_execute_is_executes_code(tmp_path: Path) -> None:
    """`self.adapter.execute("cmd")` — the 1.2a.3g shape, recognized."""
    path = _write(tmp_path, "a.py", """
def run_command(self):
    self.adapter.execute("cmd")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"executes_code"}


def test_client_post_is_http_egress(tmp_path: Path) -> None:
    """`self.client.post(url, data=d)` sends a body to a remote host."""
    path = _write(tmp_path, "a.py", """
def notify(self, url, d):
    self.client.post(url, data=d)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"http_egress"}


def test_session_post_is_http_egress(tmp_path: Path) -> None:
    """`self.session.post(url)` — a session is a client too."""
    path = _write(tmp_path, "a.py", """
def notify(self, url):
    self.session.post(url)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"http_egress"}


def test_llm_invoke_is_calls_llm(tmp_path: Path) -> None:
    """`self.llm.invoke(prompt)` — a self-held LLM handle."""
    path = _write(tmp_path, "a.py", """
def ask(self, prompt):
    self.llm.invoke(prompt)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"calls_llm"}


def test_model_chat_is_calls_llm(tmp_path: Path) -> None:
    """`self.model.chat(prompt)` — the same handle, framework-neutral."""
    path = _write(tmp_path, "a.py", """
def ask(self, prompt):
    self.model.chat(prompt)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"calls_llm"}


# --- Qualifier families (B.10a) ---
# A wrapped LLM client is reached through a self-held handle, and the
# handle is named after the thing it is. `self.llm_client.chat(...)` is
# the shape that used to match nothing — the qualifier had to be exactly
# `llm` or `model` — so the class carrying it was credited with no
# `calls_llm`, and, with the class-name rules gone (ADR-0016, decision
# 7), ended up with no kind at all. The family is the fix; the
# underscore is its boundary. The patterns live in
# `CAPABILITY_CALLS_LLM`.

def test_llm_client_chat_is_calls_llm(tmp_path: Path) -> None:
    """`self.llm_client.chat(prompt)` — the wrapped-client shape."""
    path = _write(tmp_path, "a.py", """
def ask(self, prompt):
    self.llm_client.chat(prompt)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"calls_llm"}


def test_llm_handle_invoke_is_calls_llm(tmp_path: Path) -> None:
    """`self.llm_handle.invoke(prompt)` — any `llm_*` spelling."""
    path = _write(tmp_path, "a.py", """
def ask(self, prompt):
    self.llm_handle.invoke(prompt)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"calls_llm"}


def test_model_client_complete_is_calls_llm(tmp_path: Path) -> None:
    """`self.model_client.complete(prompt)` — the same, on a model."""
    path = _write(tmp_path, "a.py", """
def ask(self, prompt):
    self.model_client.complete(prompt)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"calls_llm"}


def test_model_client_generate_is_calls_llm(tmp_path: Path) -> None:
    """`generate` is a handle verb for `model` too (B.10a)."""
    path = _write(tmp_path, "a.py", """
def ask(self, prompt):
    self.model_client.generate(prompt)
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"calls_llm"}


def test_private_llm_handles_are_calls_llm(tmp_path: Path) -> None:
    """The 1.2a.3e underscore rule runs before the prefix check."""
    path = _write(tmp_path, "a.py", """
def ask(self, prompt):
    self._llm.chat(prompt)


def ask_again(self, prompt):
    self._llm_client.invoke(prompt)
""")
    result = extract(path, "a.py")
    caps = {s.symbol_name: s.capabilities for s in result.symbols}
    assert caps["ask"] == {"calls_llm"}
    assert caps["ask_again"] == {"calls_llm"}


def test_modelviewer_is_not_calls_llm(tmp_path: Path) -> None:
    """The prefix is matched at an underscore boundary, not anywhere."""
    path = _write(tmp_path, "a.py", """
def show(self, prompt):
    self.modelviewer.invoke(prompt)
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_llmviewer_is_not_calls_llm(tmp_path: Path) -> None:
    """`llmviewer` is not a handle: `llm` plus a word is not `llm_*`."""
    path = _write(tmp_path, "a.py", """
def show(self, prompt):
    self.llmviewer.chat(prompt)
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_client_chat_is_not_calls_llm(tmp_path: Path) -> None:
    """`client` is not in the family — an HTTP client is not a model."""
    path = _write(tmp_path, "a.py", """
def notify(self, prompt):
    self.client.chat(prompt)
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_llm_client_get_is_not_calls_llm(tmp_path: Path) -> None:
    """The family widens the receiver, never the verb."""
    path = _write(tmp_path, "a.py", """
def fetch(self, prompt):
    self.llm_client.get(prompt)
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_other_receivers_are_not_calls_llm(tmp_path: Path) -> None:
    """`client.get` and `notllm.invoke` are not handles either."""
    path = _write(tmp_path, "a.py", """
def fetch(self):
    self.client.get("/x")
    self.notllm.invoke("prompt")
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_llm_handle_patterns_are_registered() -> None:
    """Every handle spelling matches, at any receiver depth."""
    for pattern in (
        "{llm,model}.invoke",
        "{llm,model}.chat",
        "{llm,model}.complete",
        "{llm,model}.generate",
    ):
        assert pattern in patterns.CAPABILITY_CALLS_LLM, pattern
    for name in (
        "self.llm.invoke",
        "self.llm_client.chat",
        "self.llm_handle.complete",
        "self.model.invoke",
        "self.model_client.generate",
    ):
        assert _matches_any(name, patterns.CAPABILITY_CALLS_LLM), name
    for name in (
        "self.client.chat",
        "self.modelviewer.invoke",
        "self.llm_client.get",
    ):
        assert not _matches_any(name, patterns.CAPABILITY_CALLS_LLM), name


def test_a_wrapped_llm_client_class_is_classified(tmp_path: Path) -> None:
    """The FinBot shape, end to end: AST → capabilities → kind.

    `InvoiceAgent` is a name from that corpus, and it decides nothing
    here: the `self.llm_client.chat(...)` in the body is what makes the
    class a node (ADR-0009; ADR-0016, decision 7). Before B.10a the body
    credited nothing, so the class disappeared from the graph.

    The kind is `agent`. `classify_class` intersects the capabilities
    with `AGENT_CAPABILITIES`, which contains `calls_llm`, so a class
    that only calls an LLM is already an agent — behaviour pinned in
    `test_classification.py::test_llm_capability_alone_is_an_agent` and
    outside B.10a's scope, which is the capability credit alone.
    """
    path = _write(tmp_path, "agents/invoice.py", """
class InvoiceAgent:
    def __init__(self, llm_client):
        self.llm_client = llm_client

    def ask(self, prompt):
        return self.llm_client.chat(prompt)
""")
    result = extract(path, "agents/invoice.py")
    caps = {s.symbol_name: s.capabilities for s in result.symbols}
    assert caps["InvoiceAgent"] == {"calls_llm"}

    classified = classify_class(
        "InvoiceAgent", "agents/invoice.py", sorted(caps["InvoiceAgent"])
    )
    assert classified is not None
    assert classified.kind == "agent"


def test_a_wrapped_llm_client_class_with_mcp_is_an_agent(
    tmp_path: Path,
) -> None:
    """`calls_llm` + `invokes_mcp_tool` — the agent trigger, no name."""
    path = _write(tmp_path, "agents/invoice.py", """
class InvoiceAgent:
    def ask(self, prompt):
        return self.llm_client.chat(prompt)

    def tools(self):
        return self.session.call_tool("list_invoices")
""")
    result = extract(path, "agents/invoice.py")
    caps = {s.symbol_name: s.capabilities for s in result.symbols}
    assert caps["InvoiceAgent"] == {"calls_llm", "invokes_mcp_tool"}

    classified = classify_class(
        "InvoiceAgent", "agents/invoice.py", sorted(caps["InvoiceAgent"])
    )
    assert classified is not None
    assert classified.kind == "agent"


def test_bare_run_is_not_executes_code(tmp_path: Path) -> None:
    """A bare verb is matched on the final segment alone — too broad.

    `open("/tmp/x")` is the anchor: it keeps the symbol in the result
    so the assertion is not an empty-set pass.
    """
    path = _write(tmp_path, "a.py", """
def run_job(self, job):
    run()
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_self_run_is_not_executes_code(tmp_path: Path) -> None:
    """The receiver is the class itself, not an adapter."""
    path = _write(tmp_path, "a.py", """
def run_job(self):
    self.run()
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_bare_post_is_not_http_egress(tmp_path: Path) -> None:
    """A bare `post(body)` is blog code as often as it is a request."""
    path = _write(tmp_path, "a.py", """
def publish(self, body):
    post(body)
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_unknown_client_method_adds_no_capability(tmp_path: Path) -> None:
    """`client.foo` is not a known adapter verb — the qualifier is not
    a licence for every method on it."""
    path = _write(tmp_path, "a.py", """
def tune(self, x):
    self.client.foo(x)
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_client_get_is_not_http_egress(tmp_path: Path) -> None:
    """`client.get` was deliberately not added (1.2a.3i decision).

    A GET reads a remote resource; the module forms (`requests.get`,
    `httpx.get`) already cover the fetch idiom. See the adapter note in
    patterns.py.
    """
    path = _write(tmp_path, "a.py", """
def fetch(self, url):
    self.client.get(url)
    open("/tmp/x")
""")
    caps = extract(path, "a.py").symbols[0].capabilities
    assert caps == {"reads_filesystem"}


def test_adapter_patterns_are_registered() -> None:
    """Every adapter shape matches, at any receiver depth."""
    for pattern in (
        "shell.run", "shell.execute", "shell.exec",
        "adapter.run", "adapter.execute", "adapter.exec", "adapter.spawn",
    ):
        assert _matches_any(
            f"self.{pattern}", patterns.CAPABILITY_EXECUTES_CODE
        ), pattern
        assert _matches_any(
            f"self.vendor.{pattern}", patterns.CAPABILITY_EXECUTES_CODE
        ), pattern
    for pattern in (
        "client.post", "client.put", "client.patch", "client.delete",
        "adapter.post", "adapter.put", "adapter.patch",
        "session.post", "session.put", "session.patch", "session.request",
    ):
        assert _matches_any(
            f"self.{pattern}", patterns.CAPABILITY_HTTP_EGRESS
        ), pattern
    for pattern in (
        "llm.invoke", "llm.chat", "llm.complete", "llm.generate",
        "model.invoke", "model.chat", "model.complete",
    ):
        assert _matches_any(
            f"self.{pattern}", patterns.CAPABILITY_CALLS_LLM
        ), pattern


def test_adapter_patterns_are_qualified_and_not_self_calls() -> None:
    """No bare adapter verb and no `self.<verb>` was added (1.2a.3i).

    `run`, `execute`, `post`, `put`, `patch`, `delete`, `request`,
    `invoke`, `chat`, `complete`, `generate` and `spawn` are far too
    common on their own, and `self.<verb>` is a plain method call on
    the class itself. The three one-segment patterns already in
    CAPABILITY_EXECUTES_CODE (`eval`, `exec`, `compile`) predate this
    item and name builtins, not adapter verbs.
    """
    bare = {
        "run", "execute", "post", "put", "patch", "delete", "request",
        "invoke", "chat", "complete", "generate", "spawn",
    }
    for name, value in vars(patterns).items():
        if not name.startswith("CAPABILITY_") or not isinstance(
            value, (tuple, frozenset)
        ):
            continue
        for pattern in value:
            assert pattern not in bare, f"{name}: {pattern}"
            assert not pattern.startswith("self."), f"{name}: {pattern}"


def test_client_get_is_not_registered() -> None:
    """The GET decision is pinned: a future item has to change it here."""
    for pattern in ("client.get", "session.get", "adapter.get"):
        assert pattern not in patterns.CAPABILITY_HTTP_EGRESS


# ------------------------------------------------------------
# Untrusted sources (1.3a)
# ------------------------------------------------------------

def test_reads_untrusted_is_credited_per_family(tmp_path: Path) -> None:
    """One capability, three source families, one rule each."""
    path = _write(tmp_path, "a.py", """
import imaplib


def read_docs(question):
    return retriever.invoke(question)


def catalogue():
    return client.list_tools()


def inbox():
    imap = imaplib.IMAP4_SSL("imap.example.com")
    return imap.search(None, "ALL")
""")
    result = extract(path, "a.py")
    caps = {s.symbol_name: s.capabilities for s in result.symbols}

    assert caps["read_docs"] == {"reads_untrusted"}
    assert caps["catalogue"] == {"reads_untrusted"}
    assert caps["inbox"] == {"reads_untrusted"}


def test_reads_untrusted_folds_into_the_class(tmp_path: Path) -> None:
    """A method's read is the class's capability, like every other."""
    path = _write(tmp_path, "a.py", """
class Tools:
    def search(self, q):
        return collection.query(q)
""")
    result = extract(path, "a.py")
    by_name = {s.symbol_name: s.capabilities for s in result.symbols}

    assert by_name["Tools"] == {"reads_untrusted"}
    assert by_name["search"] == {"reads_untrusted"}


def test_a_source_read_is_not_a_sink_capability() -> None:
    """`reads_untrusted` is a source: it must not end a chain."""
    assert "reads_untrusted" in patterns.VALID_CAPABILITIES
    assert "reads_untrusted" not in patterns.SINK_CAPABILITIES
    assert patterns.LETHAL_TRIFECTA_READ_CAPS == {"reads_untrusted"}


def test_credit_rejects_an_unknown_capability() -> None:
    """The vocabulary is closed at the one place a rule writes it."""
    with pytest.raises(ValueError, match="unknown capability"):
        _credit(
            set(),
            "reads_everything",
            patterns.READS_UNTRUSTED_RAG_RULE,
            "retriever.invoke",
        )


def test_credit_accepts_every_declared_capability() -> None:
    """Every rule that credits a node capability is in the vocabulary.

    Routing capabilities (`patterns_registry.ROUTING_CAPABILITIES`:
    `db_statement`, `commit`) are exempt by design — they never land on
    a node, so they are not in `VALID_CAPABILITIES` and `_credit` is
    never called with one.
    """
    for rule in patterns.CALL_RULES:
        if rule.capability in ROUTING_CAPABILITIES:
            continue
        if not rule.text_patterns:
            # `http_ingress` is declared but has no pattern yet (see
            # patterns.py): a rule with nothing to match credits nothing.
            continue
        caps: set[str] = set()
        _credit(caps, rule.capability, rule, rule.text_patterns[0])

        assert caps == {rule.capability}

