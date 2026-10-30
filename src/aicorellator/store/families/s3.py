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

The same stores, three other spellings of the handle (B.3b-s3-followup):

  - `s3fs.S3FileSystem().open('s3://<bucket>/<key>', mode)` — a write
    or a read, by the mode;
  - `Minio(...).put_object(bucket, key, ...)` — a write, positional;
  - `Minio(...).get_object(bucket, key)` — a read, positional.

Each name states its own direction — there is no mode argument to read,
which is why the two lists are the whole of the shape rule, and `open`
is the exception: its mode decides, the way the `fs` family's `open`
does. `Bucket` and `Key` are read by keyword when the call names them,
and at the shape's documented position when it does not (`_SHAPES`);
`put_object` and `get_object` have no positional boto3 spelling that
names a store, so on a boto3 receiver they are keyword-only here.

**The receiver, three rules that coexist.** A call is this family's when
its receiver matches one of:

  1. **a literal receiver name** — a name whose **last segment** is
     `s3`, `s3_client`, `client`, `s3_resource` or `resource`, the
     conventional bindings for an `s3` handle (B.3b-s3);
  2. **a tracked handle name** — a `Name` assigned in this file to a
     `boto3.client(...)` / `boto3.resource(...)` call, to a
     `s3fs.S3FileSystem(...)` call, or to a `Minio(...)` call
     (B.3b-s3-followup);
  3. **nothing else.** A bare `put_object(...)` is not a site: the AST
     does not know what the object is, and a receiver that is an
     expression — `boto3.client('s3').put_object(...)` — is not a name
     at all.

The literal set and the handle sets are **additive and independent**:
`client` is a literal receiver whatever it holds, and a handle set adds
names the literal set does not carry. The two rules coexist, so
`client = some_factory(); client.put_object(Bucket='docs', Key='x')` is
a site by rule 1 even though `client` is not a handle.

**The discriminator is the argument layout, not the receiver name.**
`put_object` / `get_object` belong to both boto3 and MinIO, and the two
spell them differently:

  - **boto3** takes `Bucket=` / `Key=` (uppercase) and **no positional
    arguments** — every boto3 method is `**kwargs`-only, so a positional
    argument does not name a store and the call is **not** a site;
  - **MinIO** takes the positional `(bucket, key)` or the lowercase
    `bucket=` / `key=` keywords, and an uppercase `Bucket=` / `Key=` is
    boto3's spelling, so a MinIO call carrying one is **not** a site.

The two negatives are pinned in `tests/test_store_families/test_s3.py`.

**Handle tracking is dataflow-lite, and it is not dataflow.** ADR-0001
forbids following values through calls; this step does not follow a
value. It resolves **one assignment per name per scope** — a `Name =
Call` whose call target is a constructor — and asks whether the receiver
*name* is one of those. It does not follow the handle through calls,
returns or aliases, so `client = get_client()` (a factory returning an
`s3` handle) is not a handle, and `boto3.client('s3').put_object(...)`
is not resolved. The scope rule is the same-file rule: a handle at module
level is followed from any call site in the file, a handle assigned in
the same function as the call site is followed, and a handle assigned in
**another** function is not — a false negative, and the one the scope
rule buys: the family does not build a call graph to prove which
function runs first. Two assignments to one name in one scope: the last
in source order wins. The service argument is **not** checked, so a
`boto3.client('ec2')` handle is a false positive the family accepts —
the receiver is a handle, and which service it talks to is a future
step's business.

What the family does not do, and why:

  - **It does not follow handles through calls.** A handle bound to
    another name (`c = boto3.client('s3'); c.put_object(...)`) *is*
    followed, because the assignment is in the file; a handle built by a
    factory (`c = get_client()`) is not, and neither is a handle passed
    in as a parameter. A handle assigned in another function is not
    followed either (the scope rule above), and a variable named
    `s3_client` that holds something else is a false positive of rule 1.
    The receiver set is the whole of what the AST can say without
    following values (ADR-0016).
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
constructor, not a call site), MinIO's presigned URLs
(`presigned_url` / `presigned_get_object` — a URL is not content), and
the spellings this family cannot resolve without following a value:
`open('s3://...')` with a bare `open` (the `fs` family's site — it names
a path, and it is `fs` that reports it), `s3fs.S3FileSystem().open
('s3://...')` (a direct handle, not a stored one), `fs.open('/tmp/x')`
on an `s3fs` handle (a filesystem path is not an S3 store), a `minio`
handle (case-sensitive: `Minio` is the class), and a `Minio` /
`s3fs.S3FileSystem` handle assigned in another function (the scope
rule). Each is a documented false negative.

The literals, which are §8's identity hint:

  - `Bucket` and `Key` are one hint: `'docs'` and `'uploads/x'` name
    `'docs/uploads/x'`. MinIO's positional `(bucket, key)` is the same
    two halves and the same hint;
  - an `s3fs` path is one hint with the scheme stripped: `'s3://docs/x'`
    names `'docs/x'` — the same store a boto3 `Bucket='docs',
    Key='x'` names — and `'s3://docs/'` names `'docs/'`, the bucket.
    A path that does not start with `s3://` names no S3 store at all;
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
from collections.abc import Iterator
from dataclasses import dataclass

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
#: site that names one, and a call that passes them positionally names
#: no store at all (`_boto3_shape`).
_SHAPES: dict[str, tuple[bool, int | None, int | None]] = {
    "put_object": (True, None, None),
    "upload_file": (True, 1, 2),
    "upload_fileobj": (True, 1, 2),
    "get_object": (False, None, None),
    "download_file": (False, 0, 1),
    "download_fileobj": (False, 0, 1),
}

#: MinIO's two shapes, and only these: `bucket` and `key` are positional
#: or lowercase keywords, never boto3's `Bucket` / `Key` (`_minio_shape`).
_MINIO_SHAPES: dict[str, bool] = {"put_object": True, "get_object": False}

#: The constructor calls a tracked handle is bound from, as the **tail**
#: segments of the dotted call target. `boto3.client` and `boto3.resource`
#: are matched by their last two segments, `s3fs.S3FileSystem` likewise,
#: and `Minio` is case-sensitive: `minio` is a false negative.
_BOTO3_CONSTRUCTORS = (("boto3", "client"), ("boto3", "resource"))
_S3FS_CONSTRUCTORS = (("s3fs", "S3FileSystem"),)
_MINIO_CONSTRUCTORS = (("Minio",),)

#: The `s3fs` path scheme; everything after it is `<bucket>/<key>`.
_S3_SCHEME = "s3://"

#: The mode characters that make an `open` a write, and the ones that
#: disqualify it from being a read — the `fs` family's rule, copied
#: rather than imported for the reason `_literal_prefix` is (below).
_WRITE_MODE_CHARS = ("w", "a", "x", "+")
_READ_MODE_CHARS = ("w", "a", "x")

#: The nodes that open a new scope for the handle rule: a handle
#: assigned in one function is not followed from another.
_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


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
            for call, hint, _write, read in _sites(tree)
            if read
        ]


@dataclass(frozen=True)
class _Handles:
    """The tracked handle names in scope at one call site.

    Three sets, one per technology, because a name is a handle *of one
    thing*: a `Minio` handle is not a boto3 handle, and the layouts the
    two take are different. Each set already carries the module-level
    names and the names of the call site's own function (`_scoped_names`).
    """

    boto3: frozenset[str] = frozenset()
    s3fs: frozenset[str] = frozenset()
    minio: frozenset[str] = frozenset()


def _sites(tree: ast.Module) -> list[tuple[ast.Call, str | None, bool, bool]]:
    """Every store call site in one module: `(call, hint, write, read)`.

    A scoped walk over the module (`_scoped_walk`), because the handle
    rule is scoped: the handle sets are computed once and the names a
    call site may use are the module-level ones plus its own function's.
    The sites are sorted by line and then by column to put them in
    source order — a family's report is then a property of the code and
    not of the traversal. A call that matches no shape, or matches one
    on a receiver that is not a literal `s3` name and not a tracked
    handle, is skipped: there is no site to report.
    """
    boto3 = _handle_receivers(tree, _BOTO3_CONSTRUCTORS)
    s3fs = _handle_receivers(tree, _S3FS_CONSTRUCTORS)
    minio = _handle_receivers(tree, _MINIO_CONSTRUCTORS)

    sites: list[tuple[ast.Call, str | None, bool, bool]] = []
    for node, scope in _scoped_walk(tree):
        if not isinstance(node, ast.Call):
            continue
        handles = _Handles(
            boto3=_scoped_names(boto3, scope),
            s3fs=_scoped_names(s3fs, scope),
            minio=_scoped_names(minio, scope),
        )
        shape = _classify(node, handles)
        if shape is None:
            continue
        hint, write, read = shape
        sites.append((node, hint, write, read))
    sites.sort(key=lambda site: (site[0].lineno, site[0].col_offset))
    return sites


def _classify(
    call: ast.Call, handles: _Handles
) -> tuple[str | None, bool, bool] | None:
    """What one call is, as `(hint, write, read)`, or `None`.

    Three receiver kinds, checked in order, each with its own layout:

      - an `s3fs` handle's `open` — the mode decides the direction and
        the literal path names the store (`_s3fs_shape`);
      - a MinIO handle's `put_object` / `get_object` — positional
        `(bucket, key)` or lowercase keywords (`_minio_shape`);
      - a boto3 shape on a literal receiver name or on a tracked boto3
        handle — `Bucket` / `Key` by keyword (`_boto3_shape`).

    Everything else is not this family's: another object's `put_object`,
    a bare `put_object(...)`, a `Minio` handle spelled with boto3's
    keywords, a boto3 handle called with positional arguments, and every
    call the module docstring lists as deliberately absent
    (`head_object`, `delete_object`, `put_bucket_policy`, ...).
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr
    receiver = func.value

    if method == "open" and _is_handle(receiver, handles.s3fs):
        return _s3fs_shape(call)
    if method in _MINIO_SHAPES and _is_handle(receiver, handles.minio):
        return _minio_shape(call, _MINIO_SHAPES[method])
    if method in _SHAPES and (
        _receiver_name(receiver) in _RECEIVERS
        or _is_handle(receiver, handles.boto3)
    ):
        return _boto3_shape(call, _SHAPES[method])
    return None


def _boto3_shape(
    call: ast.Call, shape: tuple[bool, int | None, int | None]
) -> tuple[str | None, bool, bool] | None:
    """A boto3 call's `(hint, write, read)`, or `None`.

    `put_object` and `get_object` have no positional spelling that names
    a store (`_SHAPES`'s two `None` indices): every boto3 method takes
    `**kwargs`, so a call that passes positional arguments is not the
    shape and names nothing — `s3_client.put_object('docs', 'x')` is not
    a site. The shapes that *do* have a positional layout keep it:
    `upload_fileobj(fileobj, 'docs', 'x')` is a site by its own rule.

    The hint is `bucket + "/" + key` (`_identity_hint`) and is `None`
    when the bucket names no literal — §5's "no store", while the site
    itself stays: the call is a fact about a store it does not name.
    """
    write, bucket_index, key_index = shape
    if bucket_index is None and key_index is None and call.args:
        return None
    hint = _identity_hint(
        _argument(call, bucket_index, "Bucket"),
        _argument(call, key_index, "Key"),
    )
    return hint, write, not write


def _minio_shape(
    call: ast.Call, write: bool
) -> tuple[str | None, bool, bool] | None:
    """A MinIO call's `(hint, write, read)`, or `None`.

    `bucket` and `key` are the **positional** first two arguments or the
    lowercase keywords; `Bucket` / `Key` are boto3's spelling and are not
    MinIO's, so a call carrying either is not this shape and names
    nothing (`mc.put_object(Bucket='docs', Key='x')` is not a site).
    """
    if (
        _keyword(call, "Bucket") is not None
        or _keyword(call, "Key") is not None
    ):
        return None
    hint = _identity_hint(
        _argument(call, 0, "bucket"), _argument(call, 1, "key")
    )
    return hint, write, not write


def _s3fs_shape(call: ast.Call) -> tuple[str | None, bool, bool] | None:
    """An `s3fs` `open`'s `(hint, write, read)`, or `None`.

    The path is the first positional or `path=`, and it has to be a
    literal starting with `s3://`: a filesystem path on an `s3fs` handle
    (`fs.open('/tmp/x', 'wb')`) is not an S3 store, and a path that is
    not a literal names nothing — both are `None`, which is §5's "no
    store" and, here, no site at all. The hint is the path with the
    scheme stripped, so `'s3://docs/x'` names the same store a boto3
    `Bucket='docs', Key='x'` names.

    The mode is the second positional or `mode=`, and it decides the
    direction by the `fs` family's rule (`_mode_direction`); a mode the
    AST cannot read is neither direction, and a call that is neither is
    no site.
    """
    path = _literal_prefix(_argument(call, 0, "path"))
    if path is None or not path.startswith(_S3_SCHEME):
        return None
    write, read = _mode_direction(_argument(call, 1, "mode"))
    if not (write or read):
        return None
    return path[len(_S3_SCHEME):], write, read


def _is_handle(expr: ast.expr, names: frozenset[str]) -> bool:
    """True when a receiver is a tracked handle `Name`.

    Only a bare `Name` can be a handle: the assignment's target is a
    `Name` (`_assignment_names`), so `self.s3_client = boto3.client('s3')`
    does not make `self.s3_client` a handle — it is a literal receiver
    by its last segment, and that rule is unchanged.
    """
    return isinstance(expr, ast.Name) and expr.id in names


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


def _handle_receivers(
    tree: ast.Module, constructors: tuple[tuple[str, ...], ...]
) -> dict[object, frozenset[str]]:
    """The handle names each constructor call binds, keyed by scope.

    **One assignment per name per scope**, resolved in source order: the
    names a `Name = Call` binds, where the call's dotted target ends in
    one of `constructors`. The value is a map from the enclosing
    `FunctionDef` / `AsyncFunctionDef` (or `None` for module level) to
    the handle names assigned there — the scope is what makes a handle
    in another function not followed (`_scoped_names`).

    Reassignment: the **last** assignment in source order decides, so a
    name assigned to a constructor and then to something else is not a
    handle, and one assigned to something else and then to a constructor
    is. Nothing is followed: the value has to be a constructor *call*,
    so a factory (`client = get_client()`) binds no handle (ADR-0001,
    the module docstring's dataflow-lite rule).
    """
    last: dict[tuple[object, str], bool] = {}
    for node, scope in _scoped_walk(tree):
        names = _assignment_names(node)
        if not names:
            continue
        is_handle = _is_constructor(_assignment_value(node), constructors)
        for name in names:
            last[(scope, name)] = is_handle

    handles: dict[object, set[str]] = {}
    for (scope, name), is_handle in last.items():
        if is_handle:
            handles.setdefault(scope, set()).add(name)
    return {scope: frozenset(names) for scope, names in handles.items()}


def _scoped_names(
    handles: dict[object, frozenset[str]], scope: object
) -> frozenset[str]:
    """The handle names visible at a call site in `scope`.

    The module-level names, plus the names of the call site's own
    function. A handle assigned in another function is not visible — the
    false negative the docstring documents.
    """
    names = handles.get(None, frozenset())
    if scope is not None:
        names = names | handles.get(scope, frozenset())
    return names


def _scoped_walk(
    node: ast.AST, scope: ast.AST | None = None
) -> Iterator[tuple[ast.AST, ast.AST | None]]:
    """Yield `(node, scope)` for every node, `scope` being the function.

    The scope of a node is its nearest enclosing `FunctionDef` /
    `AsyncFunctionDef`, or `None` at module level. The walk is
    `ast.iter_child_nodes`'s order, which is field order and therefore
    source order — the reason the "last assignment wins" rule is
    well-defined.
    """
    yield node, scope
    for child in ast.iter_child_nodes(node):
        child_scope = child if isinstance(child, _FUNCTION_NODES) else scope
        yield from _scoped_walk(child, child_scope)


def _assignment_names(node: ast.AST) -> tuple[str, ...]:
    """The plain `Name` targets of an assignment, or `()`.

    `x = ...` names `x`; `x: T = ...` names `x`; `x = y = ...` names
    both. A target that is an attribute, a subscript or a tuple is not a
    handle: the handle rule is about a name, and nothing else.
    """
    if isinstance(node, ast.Assign):
        targets: tuple[ast.expr, ...] = tuple(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = (node.target,)
    else:
        return ()
    return tuple(
        target.id for target in targets if isinstance(target, ast.Name)
    )


def _assignment_value(node: ast.AST) -> ast.expr | None:
    """The value of an assignment, or `None` (`x: int` has none)."""
    if isinstance(node, ast.Assign):
        return node.value
    if isinstance(node, ast.AnnAssign):
        return node.value
    return None


def _is_constructor(
    value: ast.expr | None, constructors: tuple[tuple[str, ...], ...]
) -> bool:
    """True when a value is a call whose dotted target ends in a tail.

    The tail comparison is on the **last** segments, so `boto3.client`
    matches `boto3.client('s3')`; the service argument is not read (the
    docstring's documented false positive).
    """
    if not isinstance(value, ast.Call):
        return False
    dotted = _dotted_name(value.func)
    if dotted is None:
        return False
    parts = dotted.split(".")
    return any(tuple(parts[-len(tail):]) == tail for tail in constructors)


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


def _dotted_name(node: ast.expr) -> str | None:
    """The dotted name of an expression, or `None`.

    `boto3.client` → `"boto3.client"`, `Minio` → `"Minio"`, and
    `boto3.client('s3')` → `None`, because its receiver is a call and
    not a name. The same walk `capability_extractor._dotted_name` does,
    copied for the reason `_literal_prefix` is: `store/` does not import
    `parsing/`.
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return ".".join(parts)
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    """The expression passed as `name=`, or `None` when absent.

    Unlike `_argument`, this does **not** fall back to a positional slot:
    the MinIO rule is about a keyword *being there at all*, and
    `Bucket=` / `Key=` are what disqualify the call (`_minio_shape`).
    """
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _mode_direction(mode: ast.expr | None) -> tuple[bool, bool]:
    """`(write, read)` for an `open` mode — the `fs` family's rule.

    An absent mode is a read, because `'r'` is `open`'s default. A
    string literal is a write when it carries any of `w`, `a`, `x`, `+`
    and a read when it carries none of `w`, `a`, `x` — so `'r+'` is
    **both**, while `'wb'` is a write only and `'rb'` a read only.
    Anything else — a parameter, an f-string — is neither: the AST
    cannot read the mode, and a direction invented from it would be a
    guess. Copied from `fs._mode_direction` for the reason
    `_literal_prefix` is.
    """
    if mode is None:
        return False, True
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return False, False
    text = mode.value
    write = any(char in text for char in _WRITE_MODE_CHARS)
    read = not any(char in text for char in _READ_MODE_CHARS)
    return write, read


__all__ = ["S3Family"]
