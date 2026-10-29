"""The `db` family — call shapes, tables, and what it ignores.

ADR-0021 §12 gives a family one job: read a module's AST once, report the
call sites that write into and read from a store it recognizes, and say
what literal each site names the store with. For `db` the store is a
table, and the literal is the table name the SQL string carries.
Everything else — identity, confidence, nodes, edges — is
`StoreResolver`'s and is pinned in
`tests/test_store_core/test_db_resolver.py`. What is here is the family's
half:

  - **the write shapes** (W1–W6): `INSERT` / `UPDATE` / `DELETE` /
    `REPLACE` / `MERGE` through `execute`, `executemany`, and the
    `sql=` / `statement=` spellings;
  - **the read shapes** (R1–R4): `SELECT`, a CTE, `EXPLAIN` and
    `PRAGMA`;
  - **the table extraction**: quoting, the first `FROM` winning, and the
    regex's documented false negatives (a quoted identifier with a
    space, a `FROM` inside a CTE body);
  - **the non-sites**: the ORM methods, the fetches, the DDL keywords, a
    `text(...)` argument and a non-literal SQL string;
  - **the receivers**: the handle names that match, underscores and all;
  - **the registration**: importing `aicorellator.store` is enough for
    the resolver to see `db`.

The family is orthogonal to the sink rules — `cursor.execute("INSERT
INTO ...")` is a `writes_db` sink today — and nothing here asserts
anything about capabilities; `patterns.py` is not this step's.
"""

from __future__ import annotations

import ast

from aicorellator.store import registry
from aicorellator.store.families.base import Family, ReadSite, WriteSite
from aicorellator.store.families.db import DbFamily
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.families.s3 import S3Family

HANDLER = "app/handler.py"
TABLE = "documents"


def _writes(source: str) -> list[WriteSite]:
    return DbFamily().detect_writes(ast.parse(source), HANDLER)


def _reads(source: str) -> list[ReadSite]:
    return DbFamily().detect_reads(ast.parse(source), HANDLER)


def _hints(sites: list) -> list[str | None]:
    return [site.identity_hint for site in sites]


# ============================================================
# The write shapes (W1–W6)
# ============================================================

def test_insert_into_is_a_write_site() -> None:
    """W1 — `INSERT INTO <table>`, with the parameters ignored."""
    sites = _writes(
        f'cursor.execute("INSERT INTO {TABLE} (id) VALUES (?)", (1,))'
    )
    assert _hints(sites) == [TABLE]
    assert [site.file_path for site in sites] == [HANDLER]
    assert [site.line for site in sites] == [1]
    assert _reads(
        f'cursor.execute("INSERT INTO {TABLE} (id) VALUES (?)", (1,))'
    ) == []


def test_update_is_a_write_site() -> None:
    """W2 — `UPDATE <table> SET ...`; the table follows the keyword."""
    source = f"conn.execute('UPDATE {TABLE} SET x=1')"
    assert _hints(_writes(source)) == [TABLE]
    assert _reads(source) == []


def test_delete_from_is_a_write_site() -> None:
    """W3 — `DELETE FROM <table>`; the `FROM` picks the table."""
    source = f"cur.execute('DELETE FROM {TABLE} WHERE id=1')"
    assert _hints(_writes(source)) == [TABLE]
    assert _reads(source) == []


def test_executemany_is_a_write_site() -> None:
    """W4 — the row argument is not the store's identity."""
    source = (
        f"cur.executemany('INSERT INTO {TABLE} (id) VALUES (?)', rows)"
    )
    assert _hints(_writes(source)) == [TABLE]
    # A write spelling, and only that (the module docstring's choice).
    assert _reads(source) == []


def test_replace_and_merge_are_write_sites() -> None:
    """W5 — the other two write keywords the brief names."""
    for source in (
        f"cursor.execute('REPLACE INTO {TABLE} (id) VALUES (1)')",
        f"cursor.execute('MERGE INTO {TABLE} USING src ON 1=1')",
    ):
        assert _hints(_writes(source)) == [TABLE], source
        assert _reads(source) == [], source


def test_the_sql_keywords_are_read_too() -> None:
    """W6 — `sql=` and `statement=`, the spellings a call may name it."""
    assert _hints(
        _writes(f"cursor.execute(sql='INSERT INTO {TABLE} (id) VALUES (?)')")
    ) == [TABLE]
    assert _hints(
        _writes(f"cursor.execute(statement='UPDATE {TABLE} SET x=1')")
    ) == [TABLE]


# ============================================================
# The read shapes (R1–R4)
# ============================================================

def test_select_is_a_read_site() -> None:
    """R1 — `SELECT * FROM <table>`."""
    sites = _reads(f"cursor.execute('SELECT * FROM {TABLE}')")
    assert _hints(sites) == [TABLE]
    assert [site.line for site in sites] == [1]
    # A read's role is the context's business, and this family has none.
    assert [site.source_type for site in sites] == [None]


def test_a_select_with_a_where_clause_is_a_read_site() -> None:
    """R2 — the first `FROM` is the table; the rest is ignored."""
    source = f"conn.execute('SELECT id FROM {TABLE} WHERE x=1')"
    assert _hints(_reads(source)) == [TABLE]
    assert _writes(source) == []


def test_a_cte_is_a_read_site() -> None:
    """R3 — `WITH` is a read, and the outer `FROM` names the store."""
    source = (
        f"cursor.execute('WITH cte AS (SELECT 1) SELECT * FROM {TABLE}')"
    )
    assert _hints(_reads(source)) == [TABLE]
    assert _writes(source) == []


def test_explain_is_a_read_site() -> None:
    """R4 — `EXPLAIN` prefixes a statement; the `FROM` still names it."""
    source = f"cursor.execute('EXPLAIN SELECT * FROM {TABLE}')"
    assert _hints(_reads(source)) == [TABLE]
    assert _writes(source) == []


def test_pragma_is_a_read_site_with_no_table() -> None:
    """`PRAGMA` is a read keyword, and it names a table without a `FROM`.

    The keyword says the call reads; the regex, which reads a `FROM`,
    finds no table, so the hint is `None` — §5's wildcard, and not a
    guessed table.
    """
    source = "cursor.execute('PRAGMA foreign_keys = ON')"
    assert _hints(_reads(source)) == [None]
    assert _writes(source) == []


# ============================================================
# The direction is the SQL keyword, and the receiver is a name
# ============================================================

def test_each_statement_states_its_own_direction() -> None:
    """`execute` is both directions; only the SQL string can decide.

    Each source is checked in *both* lists, because a site in the wrong
    one would pair a reader with a reader. The arguments are the same on
    both sides, so only the keyword can decide.
    """
    writes = (
        f"cursor.execute('INSERT INTO {TABLE} (id) VALUES (1)')",
        f"cursor.execute('UPDATE {TABLE} SET x=1')",
        f"cursor.execute('DELETE FROM {TABLE}')",
        f"cursor.execute('REPLACE INTO {TABLE} (id) VALUES (1)')",
        f"cursor.execute('MERGE INTO {TABLE} USING s ON 1=1')",
    )
    reads = (
        f"cursor.execute('SELECT * FROM {TABLE}')",
        f"cursor.execute('SELECT id FROM {TABLE} WHERE x=1')",
        f"cursor.execute('EXPLAIN SELECT * FROM {TABLE}')",
        f"cursor.execute('WITH c AS (SELECT 1) SELECT * FROM {TABLE}')",
    )
    for source in writes:
        assert _writes(source), source
        assert _reads(source) == [], source
    for source in reads:
        assert _reads(source), source
        assert _writes(source) == [], source


def test_the_keywords_are_matched_without_regard_to_case() -> None:
    """SQL keywords are case-insensitive, and the sink side uppercases.

    `capability_extractor` uppercases the string before comparing it
    with `SQL_WRITE_KEYWORDS`, so a lowercase statement is a `writes_db`
    sink; the store family reads the same string the same way.
    """
    assert _hints(
        _writes(f"cursor.execute('insert into {TABLE} (id) values (?)')")
    ) == [TABLE]
    assert _hints(
        _reads(f"cursor.execute('select * from {TABLE}')")
    ) == [TABLE]


def test_the_receiver_spellings_that_match() -> None:
    """The conventional handles for a connection, a cursor or a session.

    The last segment is what counts, so `self.cursor` and
    `db.session` match as well as the bare names; the leading
    underscore is stripped, so `self._cursor` and `_conn` name the
    same handles `cursor` and `conn` do (1.2a.3e).
    """
    for receiver in (
        "cursor", "cur", "conn", "connection", "db", "session", "engine",
        "self.cursor", "self._cursor", "self._conn", "self._session",
        "self.db.session", "self._db",
    ):
        source = f"{receiver}.execute('SELECT * FROM {TABLE}')"
        assert _hints(_reads(source)) == [TABLE], source


def test_a_bare_execute_is_not_a_site() -> None:
    """No receiver, no store: the AST does not know the object.

    `execute(...)` is a method on a name the family cannot resolve, and
    §5 prefers "no store" to a guessed one.
    """
    for source in (
        f"execute('INSERT INTO {TABLE} (id) VALUES (1)')",
        f"execute('SELECT * FROM {TABLE}')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_a_receiver_outside_the_set_is_not_a_site() -> None:
    """The receiver set is the whole of what the AST can say.

    `self.adapter.execute(...)` is some other object's statement call —
    1.2a.3g's shell-adapter shape, and not a database handle — and a
    receiver that is an expression is not a name at all. Both are the
    false negatives the module docstring lists.
    """
    for source in (
        f"self.adapter.execute('INSERT INTO {TABLE} (id) VALUES (1)')",
        f"obj.execute('SELECT * FROM {TABLE}')",
        f"cur_.execute('SELECT * FROM {TABLE}')",
        f"get_conn().execute('SELECT * FROM {TABLE}')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_verb_is_not_normalized() -> None:
    """`cursor._execute(...)` is a private method, not `execute`."""
    source = f"cursor._execute('INSERT INTO {TABLE} (id) VALUES (1)')"
    assert _writes(source) == []


# ============================================================
# The table, out of the SQL string
# ============================================================

def test_the_table_comes_back_however_it_is_quoted() -> None:
    """A bare, a double-quoted and a backticked identifier are one table.

    The regex accepts the three spellings and returns the identifier
    without its wrapper, so the store's identity does not depend on how
    the statement spelled the table.
    """
    for source in (
        f"cursor.execute('INSERT INTO {TABLE} (id) VALUES (1)')",
        f"cursor.execute('INSERT INTO \"{TABLE}\" (id) VALUES (1)')",
        f"cursor.execute('INSERT INTO `{TABLE}` (id) VALUES (1)')",
        f"cursor.execute('INSERT INTO \"{TABLE}\"')",
    ):
        assert _hints(_writes(source)) == [TABLE], source


def test_the_write_regex_takes_the_keywords_first_match() -> None:
    """`INTO` comes first, so a nested `FROM` does not name the store.

    `INSERT INTO t SELECT ... FROM x` writes `t`: the scan is left to
    right and `INTO` precedes `FROM`. `DELETE FROM t WHERE id IN
    (SELECT id FROM u)` is the same rule with the other keyword.
    """
    assert _hints(
        _writes("cursor.execute('INSERT INTO t SELECT * FROM x')")
    ) == ["t"]
    assert _hints(
        _writes(
            "cursor.execute('DELETE FROM t WHERE id IN "
            "(SELECT id FROM u)')"
        )
    ) == ["t"]


def test_a_joins_table_is_not_the_store() -> None:
    """The first `FROM` is the store; a `JOIN` is a second table.

    `FROM a, b` is the same rule: the comma-separated list starts at
    `a`, and the family does not read past it.
    """
    assert _hints(
        _reads(f"cursor.execute('SELECT * FROM {TABLE} JOIN other ON 1=1')")
    ) == [TABLE]
    assert _hints(
        _reads("cursor.execute('SELECT * FROM a, b WHERE a.id=b.id')")
    ) == ["a"]


def test_a_quoted_identifier_with_a_space_names_no_table() -> None:
    """The identifier is a word; `"my table"` fails the regex.

    A documented false negative: the table is unusual, and the AST is
    not asked to parse a quoted identifier. The site is still a write —
    the `INSERT` said so — with `None` as its hint, and the read side
    names none either.
    """
    assert _hints(
        _writes("cursor.execute('INSERT INTO \"my table\" (id) VALUES (1)')")
    ) == [None]
    assert _hints(
        _reads("cursor.execute('SELECT * FROM \"my table\"')")
    ) == [None]


def test_a_from_inside_a_cte_body_wins_when_it_comes_first() -> None:
    """The documented cost of the first-match rule, pinned.

    `WITH c AS (SELECT * FROM raw) SELECT * FROM documents` names `raw`
    here, because the match is the first `FROM` in the string and the
    family does not parse the CTE's nesting. The CTE form the brief
    names — a body with no `FROM` of its own — names the outer table.
    """
    assert _hints(
        _reads(
            f"cursor.execute('WITH c AS (SELECT * FROM raw) "
            f"SELECT * FROM {TABLE}')"
        )
    ) == ["raw"]


# ============================================================
# Unresolved SQL — §5's `None`, and no site
# ============================================================

def test_a_non_literal_sql_string_is_not_a_site() -> None:
    """`cursor.execute(sql)` names no string, so no direction and no site.

    This is the one place the family is stricter than `fs` and `s3`: the
    direction lives in the same literal the table does, so a call whose
    SQL the AST cannot read can be neither a write nor a read. §5's
    answer is silence, not a guessed direction.
    """
    for source in (
        "cursor.execute(sql)",
        "cursor.execute(sql, params)",
        "cursor.execute(get_sql())",
        "cursor.execute(SQL_INSERT_DOCUMENTS)",
        "cursor.executemany(sql, rows)",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_a_literal_prefix_names_the_direction_and_not_the_table() -> None:
    """`"INSERT INTO " + t` is a write site whose hint is `None`.

    The left side of the `+` is the literal the site names — the
    direction — and the table is on the other side, where the regex
    cannot see it. §5 keeps the store the *other* side names and lets
    this one point at it (the wildcard).
    """
    assert _hints(_writes("cursor.execute('INSERT INTO ' + t)")) == [None]
    assert _hints(
        _reads("cursor.execute('SELECT * FROM ' + t)")
    ) == [None]
    assert _writes("cursor.execute('SELECT * FROM ' + t)") == []


def test_an_fstring_names_the_constant_part_before_the_first_brace() -> None:
    """`f'INSERT INTO {t} ...'` names no table; a later brace is fine.

    When the placeholder comes before the table — the `INSERT INTO {t}`
    spelling — the prefix ends at `INTO`, so there is no table. When the
    table is literal and a *later* part is not — `f'INSERT INTO t
    ({col}) VALUES (?)'` — the prefix carries the table.
    """
    assert _hints(
        _writes("cursor.execute(f'INSERT INTO {t} (id) VALUES (?)')")
    ) == [None]
    assert _hints(
        _writes("cursor.execute(f'INSERT INTO t ({col}) VALUES (?)')")
    ) == ["t"]
    assert _hints(
        _reads("cursor.execute(f'SELECT * FROM {t}')")
    ) == [None]


# ============================================================
# `executemany`, and `WITH`
# ============================================================

def test_a_select_in_an_executemany_is_not_a_site() -> None:
    """The choice the module docstring states, pinned.

    `executemany` is a write spelling: the read shape names `execute`.
    A read keyword in it is therefore neither a write (the keyword is
    not a write keyword) nor a read (the method is not `execute`), and
    the call is no site at all.
    """
    source = f"cursor.executemany('SELECT * FROM {TABLE}', rows)"
    assert _writes(source) == []
    assert _reads(source) == []


def test_a_data_modifying_cte_is_read_as_a_read() -> None:
    """`WITH` is a read, and a `WITH ... INSERT` is the false negative.

    The outer statement of `WITH c AS (SELECT 1) INSERT INTO t SELECT *
    FROM c` is a write, and reaching `t` means parsing the CTE list,
    which this family does not do: the leading keyword is `WITH`, so the
    call is a read and the first `FROM` names `c`.
    """
    source = (
        "cursor.execute('WITH c AS (SELECT 1) INSERT INTO t "
        "SELECT * FROM c')"
    )
    assert _writes(source) == []
    assert _hints(_reads(source)) == ["c"]


# ============================================================
# Not in `db`
# ============================================================

def test_the_fetches_are_not_store_sites() -> None:
    """A fetch collects the rows of an `execute` that already ran.

    The `execute` is the store operation, and the fetch is the
    `reads_db` sink half of it (`patterns.ORM_READS_RULE`); here it
    names no table and no statement.
    """
    for source in ("cursor.fetchone()", "cursor.fetchall()",
                   "cur.fetchmany(10)"):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_orm_methods_are_not_store_sites() -> None:
    """`db.add` / `db.delete` / `session.commit` name no table.

    They are `writes_db` sinks (`patterns.ORM_WRITES_RULE`) and they
    stay sinks; a store operation needs a table, and the call names
    none — the object it holds is the table, and the AST cannot see
    inside it.
    """
    for source in (
        "db.add(obj)",
        "db.add_all(objs)",
        "db.delete(obj)",
        "db.commit()",
        "session.add(obj)",
        "session.delete(obj)",
        "session.commit()",
        "session.flush()",
        "session.merge(obj)",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_query_api_is_not_a_store_site() -> None:
    """`session.query(Document)` names a mapped class, not a table.

    Turning `Document` into its table is type resolution, not a literal
    (§5: an identity the AST cannot resolve stays unresolved).
    """
    for source in (
        "session.query(Document)",
        "session.query(Document).all()",
        "session.query(Document).filter_by(id=1).first()",
        "db.get(Document, 1)",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_ddl_keywords_are_not_store_sites() -> None:
    """A schema statement creates, drops or alters — it names no store.

    `patterns.SQL_WRITE_KEYWORDS` carries `DROP`, `CREATE`, `ALTER` and
    `TRUNCATE` for the sink side; the store family has no table to pair
    for them, so they are neither direction and not a site.
    """
    for source in (
        "cursor.execute('DROP TABLE documents')",
        "cursor.execute('CREATE TABLE documents (id int)')",
        "cursor.execute('ALTER TABLE documents ADD COLUMN x int')",
        "cursor.execute('TRUNCATE TABLE documents')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_a_text_wrapped_sql_string_is_not_a_site() -> None:
    """`text(...)` hides the string one call down — a known miss.

    SQLAlchemy 2.0 spells statements `text("...")`, and the family reads
    a literal *argument*, not inside a call. Unwrapping `text(...)` is a
    separate step (the module docstring).
    """
    for source in (
        f"conn.execute(text('SELECT * FROM {TABLE}'))",
        f"session.execute(text('INSERT INTO {TABLE} (id) VALUES (1)'))",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_a_statement_whose_keyword_is_neither_is_not_a_site() -> None:
    """No direction, no site: an empty string, a comment, a `BEGIN`."""
    for source in (
        "cursor.execute('')",
        "cursor.execute('   ')",
        "cursor.execute('-- a comment')",
        "cursor.execute('BEGIN')",
        "cursor.execute('VACUUM')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


# ============================================================
# Order, and the interface
# ============================================================

def test_the_sites_come_back_in_source_order() -> None:
    """`ast.walk` is breadth-first; the family reports source order.

    The nested write is first in the file and *second* in the walk, so
    this pins the sort rather than the traversal.
    """
    source = (
        "if flag:\n"
        "    cursor.execute('INSERT INTO a (x) VALUES (1)')\n"
        "cursor.execute('UPDATE b SET x=1')\n"
    )
    assert _hints(_writes(source)) == ["a", "b"]
    assert [site.line for site in _writes(source)] == [2, 3]


def test_two_runs_over_one_module_report_the_same_order() -> None:
    """The report is a function of the code, not of the traversal."""
    source = (
        "cursor.execute('INSERT INTO a (x) VALUES (1)')\n"
        "cursor.execute('SELECT * FROM a')\n"
        "cur.executemany('INSERT INTO b (x) VALUES (?)', rows)\n"
    )
    first = [(site.line, site.identity_hint) for site in _writes(source)]
    second = [(site.line, site.identity_hint) for site in _writes(source)]
    assert first == second == [(1, "a"), (3, "b")]


def test_the_context_is_accepted_and_unused() -> None:
    """B.3b-user_upload is the first family with a context to read."""
    tree = ast.parse("cursor.execute('INSERT INTO documents (id) VALUES (1)')")
    assert len(DbFamily().detect_writes(tree, HANDLER, context=None)) == 1
    assert len(
        DbFamily().detect_reads(
            ast.parse("cursor.execute('SELECT * FROM documents')"),
            HANDLER, context=object(),
        )
    ) == 1


def test_the_family_declares_its_name() -> None:
    """The name is the registry key and half of a store's identity (§1)."""
    assert DbFamily.name == "db"


def test_the_family_is_a_family() -> None:
    assert isinstance(DbFamily(), Family)


def test_importing_the_store_package_registers_the_family() -> None:
    """No caller names `db`; the import of the package is the registration.

    `tests/test_store_core/test_db_resolver.py` builds the resolver with
    an explicit family, so the registry is what this pins: the family
    the pipeline will find is the one this module imports.
    """
    assert registry.FAMILIES["db"] is DbFamily
    assert registry.FAMILIES["fs"] is FsFamily
    assert registry.FAMILIES["s3"] is S3Family
    assert registry.all_names() == ["db", "fs", "s3"]


def test_the_registry_resolves_the_family() -> None:
    """The resolver's default input, in the order it was asked for."""
    assert [type(f) for f in registry.resolve(["db"])] == [DbFamily]
    assert [type(f) for f in registry.resolve(["fs", "s3", "db"])] == [
        FsFamily, S3Family, DbFamily,
    ]
    assert [type(f) for f in registry.registered_families()] == [
        FsFamily, S3Family, DbFamily,
    ]
