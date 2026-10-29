"""The `db` family — SQL statement write and read call sites (ADR-0021 §12).

A store in this family is a table.
`cursor.execute("INSERT INTO documents (id) VALUES (?)", (1,))` writes
the store `('db', 'documents')`, and
`cursor.execute("SELECT * FROM documents")` reads it; the family reports
the two call sites and the table name their SQL strings carry, and
nothing else. `StoreResolver` pairs them (§8), computes the confidence,
and builds the node and the edges.

The shapes, and only these:

  - `<receiver>.execute(sql, params)` — a write or a read;
  - `<receiver>.executemany(sql, rows)` — a write.

The receiver's **last segment**, with its leading underscores stripped,
is one of `cursor`, `cur`, `conn`, `connection`, `db`, `session`,
`engine` — the conventional bindings for a connection, a cursor or a
session. Stripping is `patterns_registry.CallPattern`'s rule (1.2a.3e):
`self._cursor.execute(...)` and `cursor.execute(...)` are the same
object, a private attribute being the same object as a public one. A
bare `execute(...)` is not a site: the AST does not know what the object
is, and §5 prefers "no store" to a guessed one. The verb is never
normalized either — `cursor._execute(...)` is a private method, not
`execute`.

**The direction is the SQL string's leading keyword**, not the method
name: `INSERT` / `UPDATE` / `DELETE` / `REPLACE` / `MERGE` make a write,
`SELECT` / `PRAGMA` / `EXPLAIN` / `WITH` a read, and anything else — a
`CREATE TABLE`, a `DROP`, a comment, an empty string — is neither, so
there is no site. That is the rule `capability_extractor` already
applies to this same call site (the *sink* side, `DB_STATEMENT_RULE`
plus `SQL_WRITE_KEYWORDS` / `SQL_READ_KEYWORDS`), restated here rather
than imported: `store/` does not depend on `parsing/`. Keywords are
matched case-insensitively, because SQL keywords are and because the
sink side uppercases the string before comparing it.

Two choices the keyword rule leaves open, stated rather than implied:

  - **`executemany` is a write spelling only.** Its SQL string is a
    write in every shape the brief names (`INSERT INTO ...` with rows),
    and it is not a read spelling: a read needs `execute`. So a read
    keyword in an `executemany` call is neither a write (the keyword is
    not a write keyword) nor a read (the method is not `execute`), and
    the call is no site at all — §5's silence, not a guessed direction.
    `cur.executemany("SELECT * FROM documents")` is pinned as no site.
  - **`WITH` is a read.** A CTE statement's store is the table its
    outer `FROM` names, and this family reads the first `FROM` in the
    string. A data-modifying CTE (`WITH cte AS (...) INSERT INTO t
    ...`) is therefore classified as a read and its table is the first
    `FROM`, which is a false negative: reaching the outer statement's
    table means parsing the CTE list, and this family does not. Both
    cases are pinned.

The literal, which is §8's identity hint, and how the table comes out
of it:

  - a string constant is the SQL the regex reads;
  - an expression *built from* one takes its **left** side, which is the
    part the site names: `"INSERT INTO " + t` names the `"INSERT INTO "`
    prefix and the table is after the `+`, so the hint is `None`; an
    f-string takes the constant part before the first `{...}`, so
    `f"INSERT INTO {t} ..."` names nothing while
    `f"INSERT INTO t ({c}) VALUES (?)"` names `t`;
  - anything else — a parameter, an attribute, a call — names no SQL
    string *and no direction*, so it is not a site at all. This is the
    one place the family is stricter than `fs` and `s3`: the direction
    is read from the same literal the table is, so a call whose SQL the
    AST cannot read is neither a write nor a read.
    `cursor.execute(sql)` is invisible here, while
    `cursor.execute("INSERT INTO " + t)` is a write site whose hint is
    `None` — a site §5 keeps as a wildcard.

The table name comes from a **simple regex, not a SQL parser**:

  - **write** — the first `INTO` / `UPDATE` / `FROM` match:
    `INSERT INTO documents`, `UPDATE documents SET ...`,
    `DELETE FROM documents`, `REPLACE INTO documents`,
    `MERGE INTO documents`;
  - **read** — the first `FROM` match: `SELECT ... FROM documents`,
    `WITH cte AS (...) SELECT ... FROM documents`.

The identifier is a word, optionally wrapped in double quotes or MySQL
backticks. What that rule does *not* do, and what it costs:

  - a **JOIN**'s table is a second table the query touches, not the
    store: the first `FROM` wins, and `FROM a, b` picks `a`;
  - an `INSERT INTO t SELECT ... FROM x` picks `t`, because `INTO`
    comes first in the string (and in the alternation);
  - a `FROM` **inside** a CTE body or a subquery is picked when it comes
    first, because the match is the first one and the family does not
    parse the nesting;
  - a quoted identifier **with a space** (`INSERT INTO "my table"`)
    fails the regex — a quoted identifier must close — so the hint is
    `None`. The table is unusual, and the AST is not asked to parse it.

**The family is orthogonal to the sink rules** and does not touch them.
`cursor.execute("INSERT INTO ...")` is a `writes_db` sink *today*:
`patterns.DB_STATEMENT_RULE` matches the call, `SQL_WRITE_KEYWORDS`
reads the same keyword this family reads, and `writes_db` is in
`SINK_CAPABILITIES`. One call is both facts — a sink capability and a
store operation — the way `open(..., 'wb')` is for `fs`. The store
family reads only its own half.

**Not this family**, deliberately:

  - `db.add(obj)` / `db.delete(obj)` / `session.commit()` — ORM
    methods, no SQL string and no table name. They are `writes_db`
    sinks (`patterns.ORM_WRITES_RULE`) and they stay sinks; a store
    operation needs a table, and the call names none.
  - `session.query(Document)` — the SQLAlchemy query API names a mapped
    *class*, not a table, and turning the class into a table is type
    resolution, not a literal.
  - `cursor.fetchone()` / `cursor.fetchall()` — a fetch collects the
    rows of a query executed earlier; the `execute` is the store
    operation (and the fetch is a `reads_db` sink).
  - `conn.execute(text("SELECT * FROM documents"))` — the SQL string is
    inside a `text(...)` call, and the family reads a literal argument,
    not inside one. A known false negative; unwrapping `text(...)` is a
    separate step.
  - `self.adapter.execute("cmd")` — an `execute` whose receiver is not
    a database handle by name.

The sites come back in source order (line, then column), so a family's
report is a property of the code and not of `ast.walk`'s breadth-first
order.
"""

from __future__ import annotations

import ast
import re

from .base import Context, Family, ReadSite, WriteSite

#: The two directions, as the classifier's return value.
_WRITE = "write"
_READ = "read"

#: The SQL keywords that make a statement a write, and the ones that make
#: it a read. `patterns.SQL_WRITE_KEYWORDS` / `SQL_READ_KEYWORDS` carry
#: the same rule for the sink side; the DDL keywords a store has no name
#: for (`DROP`, `CREATE`, `ALTER`, `TRUNCATE`) are deliberately absent, so
#: such a call is neither direction and not a site.
_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE")
_READ_KEYWORDS = ("SELECT", "PRAGMA", "EXPLAIN", "WITH")

#: The statement methods that are a site, and the one a read can be:
#: `executemany` is a write spelling only (module docstring).
_EXECUTE_METHODS = ("execute", "executemany")
_READ_METHOD = "execute"

#: The receiver names a statement call is recognized on, by last segment
#: with its leading underscores stripped: `cursor` here covers
#: `self._cursor`, and `cur` covers `_cur` (1.2a.3e's rule, and the
#: spellings mcploitable's `_conn.cursor(); _cur.execute(...)` uses).
_RECEIVERS = (
    "cursor",
    "cur",
    "conn",
    "connection",
    "db",
    "session",
    "engine",
)

#: The keywords a SQL string may be passed under, after the positional
#: slot.
_SQL_KEYWORDS = ("sql", "statement")

#: The leading word of a SQL string, after leading whitespace. A leading
#: comment or parenthesis matches nothing, and names no direction.
_KEYWORD_RE = re.compile(r"\s*([A-Za-z_]+)")

#: The table a write names — the first `INTO` / `UPDATE` / `FROM` match,
#: so `INSERT INTO t SELECT ... FROM x` picks `t`. The identifier is a
#: word, optionally quoted, and a quoted one must close: `"my table"`
#: matches nothing, which is the documented false negative.
_WRITE_TABLE_RE = re.compile(
    r"\b(?:INTO|UPDATE|FROM)\s+(?:\"(\w+)\"|`(\w+)`|(\w+))",
    re.IGNORECASE,
)

#: The table a read names — the first `FROM` match. A `JOIN` spells a
#: different keyword, and `FROM a, b` picks `a`.
_READ_TABLE_RE = re.compile(
    r"\bFROM\s+(?:\"(\w+)\"|`(\w+)`|(\w+))",
    re.IGNORECASE,
)


class DbFamily(Family):
    """Database stores: a statement call, by the table its SQL names.

    Both detectors walk the module once and return the sites they
    recognize. Neither raises — a family that throws is logged and
    skipped by the resolver, and an empty list is the same answer.
    """

    name = "db"

    def detect_writes(
        self,
        tree: ast.Module,
        file_path: str,
        *,
        context: Context | None = None,
    ) -> list[WriteSite]:
        """The calls in one module that write into a database store.

        `context` is accepted and unused: B.3b-user_upload is the first
        family with a context to read.
        """
        return [
            WriteSite(
                call_site=call,
                file_path=file_path,
                line=call.lineno,
                identity_hint=hint,
            )
            for call, hint, write, _read in _sites(tree)
            if write
        ]

    def detect_reads(
        self,
        tree: ast.Module,
        file_path: str,
        *,
        context: Context | None = None,
    ) -> list[ReadSite]:
        """The calls in one module that read from a database store.

        `source_type` is left at its default `None`: the role of a read
        is the context's business, and a site with no role still links
        to the `untrusted_source` the graph built for its own line.
        """
        return [
            ReadSite(
                call_site=call,
                file_path=file_path,
                line=call.lineno,
                identity_hint=hint,
            )
            for call, hint, _write, read in _sites(tree)
            if read
        ]


def _sites(tree: ast.Module) -> list[tuple[ast.Call, str | None, bool, bool]]:
    """Every store call site in one module: `(call, hint, write, read)`.

    One `ast.walk` over the module. `ast.walk` is breadth-first, so the
    sites are sorted by line and then by column to put them in source
    order — a family's report is then a property of the code and not of
    the traversal.

    A call whose SQL string the AST cannot read is skipped: the string
    carries the direction as well as the table, so a call with no
    readable string is neither a write nor a read, and §5 prefers no
    site to a guessed one. A call whose direction *is* readable but
    whose table is not (the prefix forms) is a site with a `None` hint,
    which is the wildcard §5 keeps.
    """
    sites: list[tuple[ast.Call, str | None, bool, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        shape = _classify(node)
        if shape is None:
            continue
        hint, write, read = shape
        if not (write or read):
            continue
        sites.append((node, hint, write, read))
    sites.sort(key=lambda site: (site[0].lineno, site[0].col_offset))
    return sites


def _classify(call: ast.Call) -> tuple[str | None, bool, bool] | None:
    """What one call is, as `(hint, write, read)`, or `None`.

    The whole of the shape rule, in order: a statement method on a
    database handle; a SQL argument the AST can read; a leading keyword
    that names a direction; a read only on `execute`; and the table the
    regex finds in that string, which is `None` when the string names
    none.
    """
    method = _method(call)
    if method is None:
        return None

    sql = _literal_prefix(_sql_argument(call))
    if sql is None:
        return None

    direction = _direction(sql)
    if direction is None:
        return None
    if direction == _READ and method != _READ_METHOD:
        return None

    return _table(sql, direction), direction == _WRITE, direction == _READ


def _method(call: ast.Call) -> str | None:
    """The statement method a call names, or `None` if it names none.

    Both halves of the shape are checked here because both are names:
    the method's last segment is `execute` or `executemany`, and the
    receiver's last segment — leading underscores stripped — is one of
    `_RECEIVERS`. The verb is never normalized, the way
    `patterns_registry.CallPattern` does not normalize it either.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in _EXECUTE_METHODS:
        return None
    if _receiver_name(func.value) not in _RECEIVERS:
        return None
    return func.attr


def _receiver_name(expr: ast.expr) -> str | None:
    """The name a statement call is invoked on, underscores stripped.

    The **last segment** is what counts, so `self.cursor`,
    `db.session` and a bare `cursor` all name `cursor`, and
    `self._cursor` names the same object (1.2a.3e). Anything that is
    not a `Name` or an `Attribute` — a call, a subscript, a literal —
    names nothing, which is how a receiver built by an expression is
    rejected.
    """
    name = _last_segment(expr)
    if name is None:
        return None
    return name.lstrip("_")


def _last_segment(expr: ast.expr) -> str | None:
    """The last segment of a dotted name (`a.b.c` → `"c"`), or `None`."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None



def _sql_argument(call: ast.Call) -> ast.expr | None:
    """The SQL argument of a statement call, or `None`.

    The first positional slot first, then the `sql=` / `statement=`
    keywords — the spellings a call may name the string with
    (`cursor.execute(statement="...")`), and the positional form wins
    when both are present, which is Python's own rule. The parameter and
    the row arguments are not read: `execute(sql, params)` and
    `executemany(sql, rows)` both put the statement first, and the
    parameters are not the store's identity.
    """
    if call.args:
        return call.args[0]
    for name in _SQL_KEYWORDS:
        for kw in call.keywords:
            if kw.arg == name:
                return kw.value
    return None


def _literal_prefix(expr: ast.expr | None) -> str | None:
    """The literal a SQL expression names — §8's hint, and the direction.

    A string constant is the SQL. An expression *built from* one takes
    its **left** side, which is the part the site names:
    `"INSERT INTO " + t` names the `"INSERT INTO "` prefix, so the
    direction is a write and the table, which is after the `+`, is not
    named. An f-string takes the constant part before the first `{...}`,
    and when it starts with one it names nothing. Every other expression
    — a parameter, an attribute, a call — names nothing, which is §5's
    `None`: no SQL string, no direction, no site.

    The rule is `fs._literal_prefix`'s and `s3._literal_prefix`'s,
    copied rather than imported for the reason `fs` gives: the families
    read the same expression shapes, and the shared thing is a helper,
    not a rule either step owns for the other.
    """
    if expr is None:
        return None
    if isinstance(expr, ast.Constant):
        return expr.value if isinstance(expr.value, str) else None
    if isinstance(expr, ast.BinOp):
        return _literal_prefix(expr.left)
    if isinstance(expr, ast.JoinedStr):
        for part in expr.values:
            if isinstance(part, ast.Constant):
                return part.value if isinstance(part.value, str) else None
            if isinstance(part, ast.FormattedValue):
                return None
        return None
    return None


def _direction(sql: str) -> str | None:
    """`_WRITE`, `_READ` or `None` — the statement's leading keyword.

    The keyword is the whole of the direction rule: `INSERT`, `UPDATE`,
    `DELETE`, `REPLACE` and `MERGE` write; `SELECT`, `PRAGMA`,
    `EXPLAIN` and `WITH` read. A statement whose leading word is neither
    — a `CREATE TABLE`, a `DROP`, a comment, an empty string — has no
    direction this family can report, and §5's answer is silence.
    """
    keyword = _leading_keyword(sql)
    if keyword in _WRITE_KEYWORDS:
        return _WRITE
    if keyword in _READ_KEYWORDS:
        return _READ
    return None


def _leading_keyword(sql: str) -> str:
    """The SQL string's first word, uppercased — `""` when it has none.

    Leading whitespace is skipped, which is what lets an indented
    triple-quoted statement through; a leading comment or parenthesis is
    not, and such a string names no direction.
    """
    match = _KEYWORD_RE.match(sql)
    if match is None:
        return ""
    return match.group(1).upper()


def _table(sql: str, direction: str) -> str | None:
    """The table a statement names, or `None` when the regex finds none.

    One regex per direction, and the **first** match wins: the write
    regex reads `INTO` / `UPDATE` / `FROM`, the read regex the first
    `FROM` (module docstring for what that costs). An unquoted,
    double-quoted or backticked identifier comes back without its
    quotes.
    """
    pattern = _WRITE_TABLE_RE if direction == _WRITE else _READ_TABLE_RE
    match = pattern.search(sql)
    if match is None:
        return None
    for group in match.groups():
        if group is not None:
            return group
    return None


__all__ = ["DbFamily"]

