"""The `s3` family — call shapes, literals, and what it ignores.

ADR-0021 §12 gives a family one job: read a module's AST once, report the
call sites that write into and read from a store it recognizes, and say
what literal each site names the store with. Everything else — identity,
confidence, nodes, edges — is `StoreResolver`'s and is pinned in
`tests/test_store_core/test_s3_resolver.py`. What is here is the family's
half:

  - **the shapes** (W1–W5, R1–R3): the three writers, the three readers,
    the receiver spellings that match, and the calls that are not sites;
  - **the literals** (L1–L5): `Bucket` plus `Key`, a bucket with no key,
    `None` for an unresolved bucket, and the f-string prefix;
  - **the non-sites**: `head_object`, the enumeration and delete calls,
    the bucket-configuration calls and the constructor — different facts
    about a call, and not this family's;
  - **the registration**: importing `aicorellator.store` is enough for
    the resolver to see `s3`.

The prefix signal itself (`confidence.literal_match`) is pinned where it
lives, in `tests/test_store_core/test_confidence.py`; this file pins what
the family *feeds* it.
"""

from __future__ import annotations

import ast

from aicorellator.store import registry
from aicorellator.store.families.base import Family, ReadSite, WriteSite
from aicorellator.store.families.db import DbFamily
from aicorellator.store.families.fs import FsFamily
from aicorellator.store.families.s3 import S3Family

HANDLER = "app/handler.py"
BUCKET = "docs"
KEY = "uploads/x"


def _writes(source: str) -> list[WriteSite]:
    return S3Family().detect_writes(ast.parse(source), HANDLER)


def _reads(source: str) -> list[ReadSite]:
    return S3Family().detect_reads(ast.parse(source), HANDLER)


def _hints(sites: list) -> list[str | None]:
    return [site.identity_hint for site in sites]


# ============================================================
# The write shapes (W1–W3)
# ============================================================

def test_put_object_is_a_write_site() -> None:
    """W1 — `Bucket` and `Key` by keyword, and `Body` ignored."""
    sites = _writes(
        f's3.put_object(Bucket="{BUCKET}", Key="{KEY}", Body=payload)'
    )
    assert _hints(sites) == ["docs/uploads/x"]
    assert [site.file_path for site in sites] == [HANDLER]
    assert [site.line for site in sites] == [1]


def test_upload_file_is_a_write_site() -> None:
    """W2 — `Filename` is the local path, not the S3 key.

    The same store is named with either `Filename`, and with the
    positional spelling of the shape (`Filename, Bucket, Key`): the
    family reads `Bucket` and `Key` and nothing else.
    """
    for source in (
        's3.upload_file(Filename="local.bin", Bucket="docs", '
        'Key="uploads/x")',
        's3.upload_file(Filename="/tmp/other.bin", Bucket="docs", '
        'Key="uploads/x")',
        's3.upload_file("local.bin", "docs", "uploads/x")',
    ):
        assert _hints(_writes(source)) == ["docs/uploads/x"], source


def test_upload_fileobj_is_a_write_site() -> None:
    """W3 — `(fileobj, bucket, key)`, positional or mixed."""
    for source in (
        "s3.upload_fileobj(fileobj, 'docs', 'uploads/x')",
        "s3.upload_fileobj(fileobj, Bucket='docs', Key='uploads/x')",
    ):
        assert _hints(_writes(source)) == ["docs/uploads/x"], source
        assert _reads(source) == [], source


# ============================================================
# The read shapes (R1–R3)
# ============================================================

def test_get_object_is_a_read_site() -> None:
    """R1 — the read half of the `put_object` / `get_object` pair."""
    sites = _reads(f's3.get_object(Bucket="{BUCKET}", Key="{KEY}")')
    assert _hints(sites) == ["docs/uploads/x"]
    assert [site.line for site in sites] == [1]
    # A read's role is the context's business, and this family has none.
    assert [site.source_type for site in sites] == [None]


def test_download_file_is_a_read_site() -> None:
    """R2 — the third positional is the local path, and it is ignored."""
    for source in (
        "s3.download_file('docs', 'uploads/x', '/tmp/local')",
        "s3.download_file('docs', 'uploads/x', '/tmp/other')",
    ):
        assert _hints(_reads(source)) == ["docs/uploads/x"], source
        assert _writes(source) == [], source


def test_download_fileobj_is_a_read_site() -> None:
    """R3 — `(bucket, key, fileobj)`; the file object is not the store."""
    source = "s3.download_fileobj('docs', 'uploads/x', fileobj)"
    assert _hints(_reads(source)) == ["docs/uploads/x"]
    assert _writes(source) == []


# ============================================================
# The direction comes from the name, and the receiver from the name
# ============================================================

def test_each_name_states_its_own_direction() -> None:
    """There is no mode argument: the method name *is* the direction.

    Each shape is checked in *both* lists, because a site in the wrong
    one would pair a writer with a writer. The arguments are the same
    on both sides, so only the name can decide.
    """
    writes = (
        "s3.put_object(Bucket='docs', Key='uploads/x')",
        "s3.upload_file(Filename='local', Bucket='docs', Key='uploads/x')",
        "s3.upload_fileobj(fileobj, 'docs', 'uploads/x')",
    )
    reads = (
        "s3.get_object(Bucket='docs', Key='uploads/x')",
        "s3.download_file('docs', 'uploads/x', '/tmp/local')",
        "s3.download_fileobj('docs', 'uploads/x', fileobj)",
    )
    for source in writes:
        assert _writes(source), source
        assert _reads(source) == [], source
    for source in reads:
        assert _reads(source), source
        assert _writes(source) == [], source


def test_a_bare_call_is_not_a_site() -> None:
    """W4 — no receiver, no store: the AST does not know the object.

    `put_object(...)` is a method on a name the family cannot resolve,
    and §5 prefers "no store" to a guessed one. `boto3.client('s3')` is
    where the handle comes from, and the handle is a variable: tracking
    it is dataflow and a separate step.
    """
    for source in (
        "put_object(Bucket='docs', Key='uploads/x')",
        "get_object(Bucket='docs', Key='uploads/x')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_a_receiver_that_is_not_an_s3_name_is_not_a_site() -> None:
    """W5 — the receiver set is the whole of what the AST can say.

    `obj.put_object(...)` is some other object's method, and a receiver
    that is an expression — `boto3.client('s3').put_object(...)` — is
    not a name at all. Both are the false negatives the module
    docstring lists.
    """
    for source in (
        "obj.put_object(Bucket='docs', Key='uploads/x')",
        "store_client.put_object(Bucket='docs', Key='uploads/x')",
        "boto3.client('s3').put_object(Bucket='docs', Key='uploads/x')",
        "session.get_object(Bucket='docs', Key='uploads/x')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_receiver_spellings_that_match() -> None:
    """The conventional bindings for an `s3` handle, by last segment.

    The last segment is what counts, so `aws.s3` and `self.s3_client`
    match as well as the bare names: the receiver is read as a name,
    and the name's tail is the qualifier.
    """
    for receiver in (
        "s3", "s3_client", "client", "s3_resource", "resource",
        "self.s3_client", "aws.s3",
    ):
        source = f"{receiver}.put_object(Bucket='docs', Key='uploads/x')"
        assert _hints(_writes(source)) == ["docs/uploads/x"], source


# ============================================================
# The literals (L1–L5)
# ============================================================

def test_the_bucket_and_the_key_are_one_hint() -> None:
    """L1 — `'docs'` and `'uploads/x'` name `'docs/uploads/x'`.

    Both sides of the store name it the same way, so a write and a read
    of one object carry one hint each and the resolver can pair them.
    """
    write = "s3.put_object(Bucket='docs', Key='uploads/x')"
    read = "s3.get_object(Bucket='docs', Key='uploads/x')"
    assert _hints(_writes(write)) == _hints(_reads(read)) == ["docs/uploads/x"]


def test_a_key_with_a_trailing_slash_keeps_it() -> None:
    """L2 — the key is spelled as written, and so is the hint.

    `'uploads/'` is the directory the call names, and the hint is the
    bucket plus it: nothing is appended beyond the one separator.
    """
    assert _hints(_writes("s3.put_object(Bucket='docs', Key='uploads/')")) == [
        "docs/uploads/"
    ]


def test_a_bucket_with_no_key_names_the_bucket() -> None:
    """L3 — a call that names the bucket is a fact about that store.

    The key is the half that may be missing — the bucket is not — and
    the hint ends at the bucket plus the separator.
    """
    assert _hints(_writes("s3.put_object(Bucket='docs', Body=payload)")) == [
        "docs/"
    ]
    assert _hints(_reads("s3.get_object(Bucket='docs')")) == ["docs/"]
    # A `Key` with no `Bucket` names nothing at all (§5).
    assert _hints(_writes("s3.put_object(Key='uploads/x')")) == [None]


def test_an_unresolved_bucket_names_nothing() -> None:
    """L4 — a parameter, an attribute or a call is `None`, not a guess.

    §5 turns the `None` into "no store": the resolver has no literal to
    build one from, and a store invented from a variable would be a
    store nobody named.
    """
    for source in (
        "s3.put_object(Bucket=bucket, Key=key)",
        "s3.put_object(Bucket=bucket)",
        "s3.put_object(Bucket=settings.bucket, Key='uploads/x')",
    ):
        assert _hints(_writes(source)) == [None], source
    # The same on the read side: an unresolved half is `None` there too.
    for source in (
        "s3.get_object(Bucket=bucket, Key='uploads/x')",
        "s3.get_object(Bucket=get_bucket(), Key='uploads/x')",
        "s3.download_file(bucket, 'uploads/x', '/tmp/local')",
    ):
        assert _hints(_reads(source)) == [None], source
        assert _writes(source) == [], source


def test_an_fstring_key_contributes_its_prefix() -> None:
    """L5 — the constant part before the first `{...}` is the hint.

    `f'uploads/{name}'` names the `'uploads/'` prefix, so the store is
    the bucket plus it; `f'{base}/x'` starts with the placeholder and
    names nothing, which leaves the bucket alone (`'docs/'`).
    """
    assert _hints(
        _writes("s3.put_object(Bucket='docs', Key=f'uploads/{n}')")
    ) == ["docs/uploads/"]
    assert _hints(
        _writes("s3.put_object(Bucket='docs', Key=f'{base}/x')")
    ) == ["docs/"]
    # The left side of a `+` is the same rule, as for `fs`.
    assert _hints(
        _writes("s3.put_object(Bucket='docs', Key='uploads/' + n)")
    ) == ["docs/uploads/"]


def test_a_bucket_trailing_slash_is_not_normalised() -> None:
    """The two spellings of a bucket stay two hints.

    S3 bucket names carry no trailing slash in the API, and the family
    invents none and removes none: `'docs/'` and `'docs'` are compared
    as written, so `Bucket='docs/', Key='uploads/'` names
    `'docs//uploads/'` and `Bucket='docs', Key='uploads/'` names
    `'docs/uploads/'`. `confidence.literal_match` would relate them on
    containment; the resolver attaches only on equality (B.3b-fs-
    followup), and that is pinned in `tests/test_store_core/
    test_s3_resolver.py`.
    """
    assert _hints(_writes("s3.put_object(Bucket='docs/', Key='uploads/')")) \
        == ["docs//uploads/"]
    assert _hints(_writes("s3.put_object(Bucket='docs', Key='uploads/')")) \
        == ["docs/uploads/"]
    assert _hints(_writes("s3.put_object(Bucket='docs/')")) == ["docs//"]


# ============================================================
# Not in `s3` (N1–N5)
# ============================================================

def test_head_object_is_not_a_store_site() -> None:
    """N1 — metadata is not content, and there is nothing to pair."""
    source = "s3.head_object(Bucket='docs', Key='uploads/x')"
    assert _writes(source) == []
    assert _reads(source) == []


def test_the_enumeration_calls_are_not_store_sites() -> None:
    """N2 — a listing carries names, not data."""
    for source in (
        "s3.list_objects_v2(Bucket='docs', Prefix='uploads/')",
        "s3.list_objects(Bucket='docs')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_delete_calls_are_not_store_writes() -> None:
    """N3 — a deletion is destructive; nothing is written into a store."""
    for source in (
        "s3.delete_object(Bucket='docs', Key='uploads/x')",
        "s3.delete_objects(Bucket='docs', Delete=payload)",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_bucket_configuration_calls_are_not_store_sites() -> None:
    """N4 — a bucket's policy, ACL and versioning are not data."""
    for source in (
        "s3.put_bucket_policy(Bucket='docs', Policy=policy)",
        "s3.put_bucket_acl(Bucket='docs', ACL='private')",
        "s3.put_bucket_versioning(Bucket='docs', VersioningConfiguration=c)",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_constructor_is_not_a_call_site() -> None:
    """N5 — a handle is a variable, and the family reads names only."""
    for source in (
        "boto3.client('s3')",
        "boto3.resource('s3')",
        "s3 = boto3.client('s3')",
    ):
        assert _writes(source) == [], source
        assert _reads(source) == [], source


def test_the_other_s3_spellings_are_not_this_family() -> None:
    """`s3fs`, `MinIO` and `open('s3://...')` are different qualifiers.

    Each is a new detector and not a branch here: `s3fs` and MinIO
    spell the technology differently, and `open('s3://docs/x')` is the
    `fs` family's site — it names a path, and it is `fs` that reports
    it.
    """
    for source in (
        "s3fs.S3FileSystem().open('docs/uploads/x')",
        "minio_client.put_object('docs', 'uploads/x', fileobj)",
        "open('s3://docs/uploads/x', 'wb')",
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
        "    s3.put_object(Bucket='docs', Key='a')\n"
        "s3.put_object(Bucket='docs', Key='b')\n"
    )
    assert _hints(_writes(source)) == ["docs/a", "docs/b"]
    assert [site.line for site in _writes(source)] == [2, 3]


def test_two_runs_over_one_module_report_the_same_order() -> None:
    """The report is a function of the code, not of the traversal."""
    source = (
        "s3.put_object(Bucket='docs', Key='a')\n"
        "s3.get_object(Bucket='docs', Key='a')\n"
        "s3.upload_fileobj(fileobj, 'docs', 'b')\n"
    )
    first = [(site.line, site.identity_hint) for site in _writes(source)]
    second = [(site.line, site.identity_hint) for site in _writes(source)]
    assert first == second == [(1, "docs/a"), (3, "docs/b")]


def test_the_context_is_accepted_and_unused() -> None:
    """B.3b-user_upload is the first family with a context to read."""
    tree = ast.parse("s3.put_object(Bucket='docs', Key='uploads/x')")
    assert len(S3Family().detect_writes(tree, HANDLER, context=None)) == 1
    assert len(
        S3Family().detect_reads(
            ast.parse("s3.get_object(Bucket='docs', Key='uploads/x')"),
            HANDLER, context=object(),
        )
    ) == 1


def test_the_family_declares_its_name() -> None:
    """The name is the registry key and half of a store's identity (§1)."""
    assert S3Family.name == "s3"


def test_the_family_is_a_family() -> None:
    assert isinstance(S3Family(), Family)


def test_importing_the_store_package_registers_the_family() -> None:
    """No caller names `s3`; the import of the package is the registration.

    `tests/test_store_core/test_s3_resolver.py` builds the resolver with
    an explicit family, so the registry is what this pins: the family
    the pipeline will find is the one this module imports.
    """
    assert registry.FAMILIES["s3"] is S3Family
    assert registry.FAMILIES["fs"] is FsFamily
    # `db` joined the tree in B.3b-db; the three are what it ships.
    assert registry.all_names() == ["db", "fs", "s3"]


def test_the_registry_resolves_the_family() -> None:
    """The resolver's default input, in the order it was asked for."""
    assert [type(f) for f in registry.resolve(["s3"])] == [S3Family]
    assert [type(f) for f in registry.resolve(["fs", "s3"])] == [
        FsFamily, S3Family,
    ]
    assert [type(f) for f in registry.registered_families()] == [
        FsFamily, S3Family, DbFamily,
    ]
