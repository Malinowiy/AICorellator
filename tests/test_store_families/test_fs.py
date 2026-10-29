"""The `fs` family — call shapes, literals, and what it ignores.

ADR-0021 §12 gives a family one job: read a module's AST once, report the
call sites that write into and read from a store it recognizes, and say
what literal each site names the store with. Everything else — identity,
confidence, nodes, edges — is `StoreResolver`'s and is pinned in
`tests/test_store_core/`. What is here is the family's half:

  - **the shapes** (S1–S4): `open` by mode, `Path(...).write_*` /
    `read_*`, `aiofiles.open` by mode, and `'r+'` as *both* roles;
  - **the literals** (L1–L4): a constant, the left side of a `+` or `/`,
    the part of an f-string before its first `{...}`, and `None` for
    everything else;
  - **the non-sites**: the ADR-0009 sinks (`os.remove`, `shutil.copy`,
    `shutil.move`, `os.listdir`, `Path.unlink`, ...), which are a
    different fact about a call and not this family's;
  - **the registration**: importing `aicorellator.store` is enough for
    the resolver to see `fs`.

The prefix signal itself (`confidence.literal_match`) is pinned where it
lives, in `tests/test_store_core/test_confidence.py`; this file pins
what the family *feeds* it.
"""

from __future__ import annotations

import ast

from aicorellator.store import registry
from aicorellator.store.families.base import Family, ReadSite, WriteSite
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.families.s3 import S3Family

HANDLER = "app/handler.py"


def _writes(source: str) -> list[WriteSite]:
    return FsFamily().detect_writes(ast.parse(source), HANDLER)


def _reads(source: str) -> list[ReadSite]:
    return FsFamily().detect_reads(ast.parse(source), HANDLER)


def _hints(sites: list) -> list[str | None]:
    return [site.identity_hint for site in sites]


# ============================================================
# The shapes (S1–S4)
# ============================================================

def test_open_in_a_write_mode_is_a_write_site() -> None:
    """S1 — `open(path, 'wb')` is the canonical write."""
    sites = _writes("open('/uploads/x', 'wb')")
    assert _hints(sites) == ["/uploads/x"]
    assert [site.file_path for site in sites] == [HANDLER]
    assert [site.line for site in sites] == [1]


def test_open_in_a_read_mode_is_a_read_site() -> None:
    """S1 — and `'rb'` is the same call pointing the other way."""
    sites = _reads("open('/uploads/x', 'rb')")
    assert _hints(sites) == ["/uploads/x"]
    assert [site.line for site in sites] == [1]
    # A read's role is the context's business, and this family has none.
    assert [site.source_type for site in sites] == [None]


def test_the_mode_decides_the_direction() -> None:
    """S1 — the mode characters, one by one, on both sides.

    `w`, `a` and `x` make a write and nothing else — and they keep that
    direction when a `+` joins them (`'w+'` writes, it does not read).
    A mode with none of `w`, `a`, `x` makes a read and nothing else.
    Each mode is checked in *both* lists, because a site in the wrong
    one would pair a writer with a writer. A bare `+` and the `r+`
    family are the exception and have their own test.
    """
    for mode in ("w", "wb", "a", "ab", "x", "xb", "w+", "a+", "x+"):
        assert _writes(f"open('/uploads/x', '{mode}')"), mode
        assert _reads(f"open('/uploads/x', '{mode}')") == [], mode
    for mode in ("r", "rb", "rt", "br"):
        assert _reads(f"open('/uploads/x', '{mode}')"), mode
        assert _writes(f"open('/uploads/x', '{mode}')") == [], mode


def test_a_plus_mode_is_both_a_write_and_a_read() -> None:
    """S4 — `'r+'` asks for the other half, so one call site is two roles.

    The two roles are one call site, and a group is two components: the
    resolver pairs *sites*, so this site pairs with another one or not
    at all. The `r+` family is the case that matters — the `+` asks for
    the other half of an otherwise-read mode — and a bare `'+'` is both
    for the same reason. `'w+'` is not: `w` already decides it.
    """
    for mode in ("r+", "rb+", "r+b", "+"):
        source = f"open('/uploads/x', '{mode}')"
        assert _hints(_writes(source)) == ["/uploads/x"], mode
        assert _hints(_reads(source)) == ["/uploads/x"], mode


def test_an_absent_mode_is_a_read() -> None:
    """`'r'` is `open`'s default, so `open(path)` reads."""
    assert _hints(_reads("open('/uploads/x')")) == ["/uploads/x"]
    assert _writes("open('/uploads/x')") == []


def test_the_mode_may_be_a_keyword() -> None:
    """`mode=` is the argument's name; the rule does not read positions."""
    assert _writes("open('/uploads/x', mode='wb')")
    assert _reads("open('/uploads/x', mode='rb')")


def test_the_path_may_be_a_keyword() -> None:
    """`file=` too, and the hint is the same."""
    assert _hints(_writes("open(file='/uploads/x', mode='wb')")) == [
        "/uploads/x"
    ]


def test_a_mode_the_ast_cannot_read_is_no_site() -> None:
    """A variable mode is neither direction: the AST cannot say (§5).

    The same rule the sink extractor follows: a `writes_filesystem`
    capability needs a mode string, and a direction invented from a name
    would be a guess.
    """
    assert _writes("open('/uploads/x', mode)") == []
    assert _reads("open('/uploads/x', mode)") == []
    assert _writes("open('/uploads/x', f'{mode}')") == []


def test_path_write_methods_are_write_sites() -> None:
    """S2 — the name states the direction; the receiver holds the path."""
    sites = _writes("Path('/uploads/x').write_text(data)")
    assert _hints(sites) == ["/uploads/x"]
    assert [site.line for site in sites] == [1]
    assert _hints(_writes("Path('/uploads/x').write_bytes(data)")) == [
        "/uploads/x"
    ]


def test_path_read_methods_are_read_sites() -> None:
    """S2 — and the other two read."""
    assert _hints(_reads("Path('/uploads/x').read_text()")) == [
        "/uploads/x"
    ]
    assert _hints(_reads("Path('/uploads/x').read_bytes()")) == [
        "/uploads/x"
    ]


def test_pathlib_path_is_the_same_class() -> None:
    """`pathlib.Path('/x')` is `Path('/x')`, spelled out."""
    assert _writes("pathlib.Path('/uploads/x').write_text(data)")
    assert _reads("pathlib.Path('/uploads/x').read_text()")


def test_path_open_takes_its_mode_first() -> None:
    """`Path.open`'s path is the receiver's, its mode the first argument.

    The argument layout is the reason `_classify` has a branch per shape:
    `open`'s mode is its *second* argument and `Path.open`'s is its
    first, because the receiver already holds the path.
    """
    assert _writes("Path('/uploads/x').open('wb')")
    assert _reads("Path('/uploads/x').open('rb')")
    assert _reads("Path('/uploads/x').open()")
    assert _writes("Path('/uploads/x').open(mode='wb')")


def test_a_receiver_that_is_not_a_path_call_is_not_matched() -> None:
    """The receiver must be built from `Path(...)` — the stated limit.

    `p` is a `Name` and `client` is some other class entirely; neither
    is `pathlib.Path`, so neither is a site. The hint would be `None`
    for `p` and the call would be a false positive for `client`, and §5
    prefers the missing site to the invented one.
    """
    assert _writes("p.write_text(data)") == []
    assert _reads("p.read_text()") == []
    assert _writes("client.write_text(data)") == []
    assert _reads("client.read_text()") == []


def test_builtins_open_is_the_same_call() -> None:
    """`builtins.open` is `open`, spelled out; `os.open` is not (above)."""
    assert _hints(_writes("builtins.open('/uploads/x', 'wb')")) == [
        "/uploads/x"
    ]
    assert _hints(_reads("builtins.open('/uploads/x', 'rb')")) == [
        "/uploads/x"
    ]


def test_a_path_division_names_the_directory_it_started_from() -> None:
    """`/` adds a segment to a directory; the directory is the hint."""
    assert _hints(_writes("(Path('/uploads') / 'x').write_text(data)")) == [
        "/uploads"
    ]
    assert _hints(
        _reads("(Path('/uploads') / name).read_text()")
    ) == ["/uploads"]


def test_aiofiles_open_is_matched_by_its_module() -> None:
    """S3 — `aiofiles.open(path, mode)`, the same mode rule."""
    assert _hints(_writes("aiofiles.open('/uploads/x', 'wb')")) == [
        "/uploads/x"
    ]
    assert _hints(_reads("aiofiles.open('/uploads/x', 'rb')")) == [
        "/uploads/x"
    ]


def test_an_aliased_aiofiles_is_a_known_false_negative() -> None:
    """`import aiofiles as aio` — the family follows dotted names only."""
    assert _writes("aio.open('/uploads/x', 'wb')") == []
    assert _reads("aio.open('/uploads/x', 'rb')") == []


# ============================================================
# The literals (L1–L4)
# ============================================================

def test_a_constant_is_the_whole_hint() -> None:
    """L1 — the file, exactly as written."""
    assert _hints(_writes("open('/uploads/report.txt', 'wb')")) == [
        "/uploads/report.txt"
    ]


def test_a_concatenation_names_its_prefix() -> None:
    """L2 — `'/uploads/' + name` names the directory, not the file.

    The right side is runtime data; the left side is the bucket the call
    writes into, and that is the identity a reader can be paired with.
    """
    assert _hints(_writes("open('/uploads/' + name, 'wb')")) == [
        "/uploads/"
    ]
    assert _hints(_reads("open('/uploads/' + name, 'rb')")) == [
        "/uploads/"
    ]


def test_a_chain_of_concatenations_names_the_first_literal() -> None:
    """L2 — the left side is taken again, so the prefix is the head."""
    assert _hints(_writes("open('/uploads/' + kind + '/' + name, 'wb')")) == [
        "/uploads/"
    ]


def test_an_fstring_names_the_part_before_the_braces() -> None:
    """L3 — `f'/uploads/{name}'` names `'/uploads/'`."""
    assert _hints(_writes("open(f'/uploads/{name}', 'wb')")) == [
        "/uploads/"
    ]


def test_an_fstring_that_starts_with_a_brace_names_nothing() -> None:
    """L4 — there is no leading constant to take.

    The site is still a read or a write; it just names no store, and §5
    is what makes that "no store" rather than "any store".
    """
    assert _hints(_writes("open(f'{base}/{name}', 'wb')")) == [None]
    assert _hints(_reads("open(f'{base}/{name}', 'rb')")) == [None]


def test_a_name_names_nothing() -> None:
    """L4 — a parameter is a value the AST does not hold (§5).

    `open(path, 'wb')` is a write site with no hint; the resolver keeps
    it and lets the literal side decide the store.
    """
    sites = _writes("open(path, 'wb')")
    assert _hints(sites) == [None]
    assert [site.line for site in sites] == [1]


def test_an_attribute_names_nothing() -> None:
    """L4 — `settings.upload_dir` is resolved at runtime.

    The site is still a write — the call is `open` in a write mode — and
    the hint is `None`, which is §5's answer and not the absence of a
    site.
    """
    assert _hints(_writes("open(settings.upload_dir, 'wb')")) == [None]


def test_a_call_names_nothing() -> None:
    """L4 — `os.path.join(...)` is a call, not a literal."""
    assert _hints(_writes("open(os.path.join(base, name), 'wb')")) == [None]


def test_a_percent_format_names_its_template() -> None:
    """Any expression built from a literal takes its left side.

    `'/uploads/%s' % name` is a `BinOp` like `+` and `/` are, so the
    template is the hint. It is not normalised into `'/uploads/'`: the
    family reports what the code says.
    """
    assert _hints(_writes("open('/uploads/%s' % name, 'wb')")) == [
        "/uploads/%s"
    ]


def test_a_non_string_constant_names_nothing() -> None:
    """A bytes path is not a store identity this family can state."""
    assert _hints(_writes("open(b'/uploads/x', 'wb')")) == [None]


# ============================================================
# The non-sites — ADR-0009's sinks, and `os.open`
# ============================================================

def test_os_remove_is_not_a_store_site() -> None:
    """A sink (`writes_filesystem`), and no data moves into a store."""
    assert _writes("os.remove('/uploads/x')") == []
    assert _reads("os.remove('/uploads/x')") == []


def test_shutil_copy_is_not_a_store_site() -> None:
    """A sink on both sides: it reads a file and writes one."""
    assert _writes("shutil.copy(src, '/uploads/x')") == []
    assert _reads("shutil.copy(src, '/uploads/x')") == []


def test_shutil_move_is_not_a_store_site() -> None:
    assert _writes("shutil.move(src, '/uploads/x')") == []
    assert _reads("shutil.move(src, '/uploads/x')") == []


def test_os_listdir_is_not_a_store_site() -> None:
    """Names, not content: a listing carries no data to pair."""
    assert _writes("os.listdir('/uploads')") == []
    assert _reads("os.listdir('/uploads')") == []


def test_path_unlink_is_not_a_store_site() -> None:
    """`Path.unlink` deletes; nothing is written and nothing is read."""
    assert _writes("Path('/uploads/x').unlink()") == []
    assert _reads("Path('/uploads/x').unlink()") == []


def test_os_open_is_not_the_builtin() -> None:
    """`os.open` returns a file descriptor, and it is not `open`.

    The sink rules list it separately (`os.write`), and the store model
    has nothing to pair: the descriptor is not a path literal.
    """
    assert _writes("os.open('/uploads/x', os.O_WRONLY)") == []
    assert _reads("os.open('/uploads/x', os.O_RDONLY)") == []


def test_the_rest_of_the_sink_set_is_not_a_store_site() -> None:
    """The other calls the brief names, in one place.

    `os.unlink`, `os.rmdir`, `os.rename`, `Path.rename`, `Path.iterdir`,
    `os.scandir`, `shutil.rmtree` and `shutil.copytree`: each is a sink
    or names no content, and none of them is a store operation.
    """
    calls = (
        "os.unlink('/uploads/x')",
        "os.rmdir('/uploads')",
        "os.rename(a, b)",
        "Path('/uploads').iterdir()",
        "os.scandir('/uploads')",
        "shutil.rmtree('/uploads')",
        "shutil.copytree(a, b)",
    )
    for call in calls:
        assert _writes(call) == [], call
        assert _reads(call) == [], call
    assert _writes("Path('/uploads/x').rename('/uploads/y')") == []


# ============================================================
# Order, and the interface
# ============================================================

def test_the_sites_come_back_in_source_order() -> None:
    """`ast.walk` is breadth-first; the family reports source order.

    The nested write is first in the file and *second* in the walk, so
    this pins the sort rather than the traversal.
    """
    source = "if flag:\n    open('/a', 'wb')\nopen('/b', 'wb')\n"
    assert _hints(_writes(source)) == ["/a", "/b"]


def test_the_context_is_accepted_and_unused() -> None:
    """B.3b-user_upload is the first family with a context to read."""
    tree = ast.parse("open('/uploads/x', 'wb')")
    assert len(FsFamily().detect_writes(tree, HANDLER, context=None)) == 1
    assert len(
        FsFamily().detect_reads(ast.parse("open('/uploads/x', 'rb')"),
                                HANDLER, context=object())
    ) == 1


def test_the_family_declares_its_name() -> None:
    """The name is the registry key and half of a store's identity (§1)."""
    assert FsFamily.name == "fs"


def test_the_family_is_a_family() -> None:
    assert isinstance(FsFamily(), Family)


def test_importing_the_store_package_registers_the_family() -> None:
    """P1 — no caller names `fs`; the import is the registration.

    `tests/test_store_core/test_resolver.py` builds the resolver with an
    explicit family, so the registry is what this pins: the family the
    pipeline will find is the one this module defines.
    """
    assert registry.FAMILIES["fs"] is FsFamily
    # `s3` joined the tree in B.3b-s3; the two are the families it ships.
    assert registry.all_names() == ["fs", "s3"]


def test_the_registry_resolves_the_family() -> None:
    """The resolver's default input, in the order it was asked for."""
    assert [type(f) for f in registry.resolve(["fs"])] == [FsFamily]
    assert [type(f) for f in registry.registered_families()] == [
        FsFamily, S3Family,
    ]
