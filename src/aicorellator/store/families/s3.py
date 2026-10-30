"""The `s3` family — S3 object write and read call sites (ADR-0021 §12).

A store in this family is a bucket and a key.
`s3_client.put_object(Bucket='docs', Key='uploads/x', Body=payload)`
writes the store `('s3', 'docs/uploads/x')`, and
`s3_client.get_object(Bucket='docs', Key='uploads/x')` reads it; the
family reports the two call sites and the literal they carry, and
nothing else. `StoreResolver` pairs them (§8), computes the confidence,
and builds the node and the edges.

The shapes, and only these:

  - `put_object(Bucket=..., Key=...)` — a write;
  - `upload_file(Filename, Bucket, Key)` — a write;
  - `upload_fileobj(fileobj, bucket, key)` — a write, positional;
  - `get_object(Bucket=..., Key=...)` — a read;
  - `download_file(bucket, key, local_path)` — a read, positional;
  - `download_fileobj(bucket, key, fileobj)` — a read, positional.

Each name states its own direction — there is no mode argument to read,
which is why the two lists are the whole of the shape rule. `Bucket`
and `Key` are read by keyword when the call names them, and at the
shape's documented position when it does not (`_SHAPES`); `put_object`
and `get_object` have no positional spelling that names a store, so
they are keyword-only here.

The receiver is a name whose **last segment** is `s3`, `s3_client`,
`client`, `s3_resource` or `resource` — the conventional bindings for a
`boto3.client('s3')` / `boto3.resource('s3')` handle. A bare
`put_object(...)` is not a site: the AST does not know what the object
is, and a receiver that is an expression — `boto3.client('s3').
put_object(...)` — is not a name at all.

What the family does not do, and why:

  - **It does not follow handles.** `s3_client = boto3.client('s3')`
    followed by `s3_client.put_object(...)` matches on the *name* of
    the receiver, not on what it holds, so a handle bound to another
    name (`c = boto3.client('s3'); c.put_object(...)`) is a false
    negative and a variable named `s3_client` that holds something else
    is a false positive. The receiver set is the whole of what the AST
    can say; resolving a handle is dataflow (ADR-0016) and a separate
    step.
  - **It does not read `Filename`.** `upload_file(Filename=...)` is the
    *local* path and not the S3 key: the store's identity is `Bucket`
    plus `Key`. `download_file`'s third positional is the local path
    for the same reason, and it is ignored.
  - **It does not touch the sink rules.** `put_object` is a store
    operation here, and it is not a sink today: `patterns.
    HTTP_EGRESS_RULE` names `requests` / `httpx` / `aiohttp` /
    `urllib.request` and the `client.` / `session.` / `adapter.`
    wrappers, and `boto3` is a provider SDK whose sink status is a
    separate decision (ADR-0009). One call can be both facts —
    `open(..., 'wb')` is a filesystem sink and an `fs` store operation
    — and this family reads only its own.
  - **It does not decide the role of a read.** `ReadSite.source_type`
    stays `None`: the role of a read (`user_upload`, `rag_corpus`) is
    the context's business (§1, B.3b-user_upload-core), and this family
    has no context to read.

**Not this family**, deliberately: `head_object` (metadata, not
content), `list_objects_v2` / `list_objects` (enumeration, not a read),
`delete_object` / `delete_objects` (destructive, not a store write),
`put_bucket_policy` / `put_bucket_acl` / `put_bucket_versioning`
(bucket configuration, not data), `boto3.client` / `boto3.resource` (a
constructor, not a call site), and the other S3 spellings — `s3fs`,
`MinIO`, `open('s3://...')` — which are different qualifiers and later
steps.

The literals, which are §8's identity hint:

  - `Bucket` and `Key` are one hint: `'docs'` and `'uploads/x'` name
    `'docs/uploads/x'`;
  - a `Bucket` with no `Key` names the bucket — `'docs'` names
    `'docs/'` — while a `Key` with no `Bucket` names nothing: `None` is
    the honest answer for the half that is missing, and §5 turns it
    into "no store" rather than into a guessed one;
  - an expression built from a literal takes its **left** side, as for
    `fs`: `'uploads/' + name` names `'uploads/'`, and
    `f'uploads/{name}'` takes the constant part before the first
    `{...}`, so `f'{base}/x'` names nothing;
  - **no trailing slash is invented and none is removed.** A bucket
    spelled `'docs/'` keeps it, so `Bucket='docs/', Key='uploads/'`
    names `'docs//uploads/'` — a different hint from
    `Bucket='docs', Key='uploads/'`, which names `'docs/uploads/'`.
    S3 bucket names carry no trailing slash in the API, which is why
    the two spellings stay distinct here instead of being normalised;
    the rule is the `fs` one, restated: `confidence.literal_match`
    compares the hints as written.

The sites come back in source order (line, then column), so a family's
report is a property of the code and not of `ast.walk`'s breadth-first
order.

One limitation the resolver inherits and does not fix here: a site
attaches only to the store its literal *equals*, so a write naming
`'docs/'` and a read naming `'docs/uploads/x'` are two stores and the
containment branch of `literal_match` never fires for them — the same
`fs` limitation (B.3b-fs-followup). The branch itself is pinned in
`tests/test_store_core/test_confidence.py`; the attachment decision is
the resolver's.
"""

from __future__ import annotations

import ast

from .base import Context, Family, ReadSite, WriteSite

#: The receivers a `boto3` `s3` handle is conventionally bound to, by
#: the **last** segment of the receiver name. `self.s3_client` matches;
#: a receiver that is a call does not (`_receiver_name`).
_RECEIVERS = ("s3", "s3_client", "client", "s3_resource", "resource")

#: Every shape: call name → `(write, positional Bucket, positional Key)`.
#: The indices are the shape's documented layout, used when the call
#: does not pass `Bucket` / `Key` by keyword — `upload_file` is
#: `(Filename, Bucket, Key)` and `upload_fileobj` is
#: `(fileobj, bucket, key)`, which is why the two offsets differ.
#: `None` means the shape has no positional spelling that names a
#: store: `put_object` and `get_object` are keyword-only in every call
#: site that names one.
_SHAPES: dict[str, tuple[bool, int | None, int | None]] = {
    "put_object": (True, None, None),
    "upload_file": (True, 1, 2),
    "upload_fileobj": (True, 1, 2),
    "get_object": (False, None, None),
    "download_file": (False, 0, 1),
    "download_fileobj": (False, 0, 1),
}


class S3Family(Family):
    """S3 object stores: the `Bucket` / `Key` literals a call names.

    Both detectors walk the module once and return the sites they
    recognize. Neither raises — a family that throws is logged and
    skipped by the resolver, and an empty list is the same answer.
    """

    name = "s3"

    def detect_writes(
        self,
        tree: ast.Module,
        file_path: str,
        *,
        context: Context | None = None,
    ) -> list[WriteSite]:
        """The calls in one module that write an S3 object.

        `context` is accepted and unused: B.3b-user_upload-core is the
        first family with a context to read, and an S3 store's identity
        is the literal the call carries.
        """
        return [
            WriteSite(
                call_site=call,
                file_path=file_path,
                line=call.lineno,
                identity_hint=hint,
            )
            for call, hint, write in _sites(tree)
            if write
        ]

    def detect_reads(
        self,
        tree: ast.Module,
        file_path: str,
        *,
        context: Context | None = None,
    ) -> list[ReadSite]:
        """The calls in one module that read an S3 object.

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
            for call, hint, _write in _sites(tree)
            if not _write
        ]


def _sites(tree: ast.Module) -> list[tuple[ast.Call, str | None, bool]]:
    """Every store call site in one module: `(call, hint, write)`.

    One `ast.walk` over the module. `ast.walk` is breadth-first, so the
    sites are sorted by line and then by column to put them in source
    order — a family's report is then a property of the code and not of
    the traversal. A call that matches no shape, or matches one on a
    receiver that is not an `s3` name, is skipped: there is no site to
    report.
    """
    sites: list[tuple[ast.Call, str | None, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        shape = _classify(node)
        if shape is None:
            continue
        bucket, key, write = shape
        sites.append((node, _identity_hint(bucket, key), write))
    sites.sort(key=lambda site: (site[0].lineno, site[0].col_offset))
    return sites


def _classify(
    call: ast.Call,
) -> tuple[ast.expr | None, ast.expr | None, bool] | None:
    """What one call is, as `(bucket, key, write)`, or `None`.

    The method name has to be one of the shapes *and* the receiver has
    to be an `s3` name. Everything else is not this family's: another
    object's `put_object`, a bare `put_object(...)`, and every call the
    module docstring lists as deliberately absent (`head_object`,
    `delete_object`, `put_bucket_policy`, ...).
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    shape = _SHAPES.get(func.attr)
    if shape is None or not _is_s3_receiver(func.value):
        return None
    write, bucket_index, key_index = shape
    return (
        _argument(call, bucket_index, "Bucket"),
        _argument(call, key_index, "Key"),
        write,
    )


def _is_s3_receiver(expr: ast.expr) -> bool:
    """True when a call's receiver is a name ending in an `s3` spelling.

    The **last** segment is what counts, so `s3.put_object`,
    `s3_client.put_object` and `self.s3_client.put_object` all match,
    while `boto3.client('s3').put_object(...)` does not: its receiver
    is a call, and a call is not a name the family resolves (the module
    docstring's handle-tracking false negative).
    """
    return _receiver_name(expr) in _RECEIVERS


def _receiver_name(expr: ast.expr) -> str | None:
    """The last segment of a receiver that is a name, or `None`.

    `s3` → `"s3"`, `self.s3_client` → `"s3_client"`, and
    `boto3.client('s3')` → `None`, because its receiver is a call.
    Anything that is not a `Name` or an `Attribute` is `None` with it.
    """
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _identity_hint(
    bucket: ast.expr | None, key: ast.expr | None
) -> str | None:
    """The store a call names — `bucket + "/" + key`, or `None` (§8, §5).

    `Bucket` is the half that cannot be missing: without a literal
    bucket there is no store to name, and the call is a site with
    `identity_hint=None` — §5's "no store", not a guessed one. A bucket
    with no literal key names the bucket itself (`'docs/'`), because a
    call that names the bucket is a fact about that store.

    No trailing slash is invented and none is removed: a bucket spelled
    `'docs/'` keeps it, so `('docs/', 'uploads/')` names
    `'docs//uploads/'`, and `confidence.literal_match` compares the
    hints as written.
    """
    bucket_hint = _literal_prefix(bucket)
    if bucket_hint is None:
        return None
    key_hint = _literal_prefix(key)
    if key_hint is None:
        return bucket_hint + "/"
    return f"{bucket_hint}/{key_hint}"


def _argument(
    call: ast.Call, index: int | None, keyword: str
) -> ast.expr | None:
    """The `keyword` argument, else the positional one at `index`.

    The keyword is read first: the shapes that mix the two spellings —
    `upload_fileobj(fileobj, Bucket='docs', Key='uploads/x')` — name
    the store's halves by keyword, and the positional slot is the
    fallback for the forms that pass them in order. `index` is `None`
    for the shapes with no positional spelling (`_SHAPES`), and then
    the keyword is the only place the argument can be.
    """
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    if index is not None and len(call.args) > index:
        return call.args[index]
    return None


def _literal_prefix(expr: ast.expr | None) -> str | None:
    """The literal a `Bucket` / `Key` expression names — §8's hint.

    A string constant is the hint. An expression *built from* one takes
    its **left** side, which is the prefix the site names:
    `'uploads/' + name` names the `'uploads/'` prefix, exactly as the
    `fs` family reads a path. No trailing slash is invented and none is
    removed — the hints are compared as written
    (`confidence.literal_match`), so `'uploads/'` and `'uploads'` stay
    two different hints. An f-string takes the constant part before the
    first `{...}`; when it starts with one, it names nothing. Every
    other expression — a parameter, an attribute, a call — names
    nothing, which is §5's `None`.

    The rule is `fs._literal_prefix`'s, copied rather than imported:
    the two families read the same expression shapes, and the shared
    thing is a helper, not a rule either step owns for the other.
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


__all__ = ["S3Family"]
