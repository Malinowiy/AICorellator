"""FastAPI uploads — a route handler that accepts a file (B.3b).

ADR-0021 §8 (revision 2) makes an entry-point handler that *accepts* an
upload a **context-based writer**: the stores it writes into are its
stores at base confidence 0.5 instead of 0.3. `UploadDetector` is the
interface that answers "does this handler accept an upload" — one module
per framework, ADR-0011's shape — and this is the first implementation.

Three shapes, and only these (FastAPI's own documentation):

    async def upload(file: UploadFile)          → site
    async def upload(files: list[UploadFile])   → site
    async def upload(file: bytes = File(...))   → site

A parameter is an upload if **either** its annotation is `UploadFile` —
spelled `UploadFile`, `fastapi.UploadFile`, or wrapped in one of the
containers FastAPI accepts for a multi-file field — **or** its default is
`File(...)` and its annotation is `bytes` or absent (FastAPI infers
`bytes` from the marker itself). A `Form(...)` default is never an
upload, whatever its annotation: `Form` marks a form field, and a form
field is not a file.

`request.form()` and `await request.body()` are **not** here. That upload
is a call in the body rather than a parameter, and it is
`B.3b-user_upload-fastapi-followup`'s step.

**The route check is not this detector's.** `entry_points.EntryPointStage`
already decided which functions are routes — a decorator whose last
segment is an HTTP method, carrying a literal path — and the node it
built carries the handler's name. The detector matches a function's name
against those nodes, and *confirms* with what it sees: a plain
`async def upload(file: UploadFile)` helper is not a route, is not
reachable by an HTTP client, and claiming it as a writer would claim a
store write no attacker can reach. So a site is emitted only when the
scan built an entry point for the name **and** the function carries a
route-shaped decorator. On a scan the `entry_points` stage produced, the
second condition cannot subtract anything: that stage records a handler
only after it saw such a decorator, so the two agree by construction.

What it does not do, and why:

  - **It does not follow an import alias.** `from fastapi import
    UploadFile as UF`, then `file: UF`: the annotation's last segment is
    `UF`, and the AST holds no binding from `UF` to `UploadFile`, so this
    is a false negative rather than a guess — the same limit the `fs`
    family has for `import aiofiles as aio` (ADR-0016: no assignment
    chains).
  - **`Optional[UploadFile]` is not an upload.** The container set is
    FastAPI's multi-file shapes and not every subscript, so an annotation
    whose slice is not `UploadFile` itself is not read here. A false
    negative, stated.
  - **It does not read the body**, so a handler that takes the raw
    request and parses it itself is not an upload site yet.
  - **One site per handler**, not per parameter: a handler with two
    `UploadFile` parameters accepts one upload, and two sites for it
    would be two edges into one store.

The sites come back in source order (`FunctionDef.lineno`), so two runs
over one file are identical — the same property `fs.FsFamily` gives its
call sites, and the reason the context's dedup is the only thing that
decides between two detectors of one framework.
"""

from __future__ import annotations

import ast

from .....graph.models import Node
from ..base import UploadDetector, UploadSite

#: The registry key, and the `framework` every site is stamped with. The
#: class declares the same name, so the registry's name-vs-class warning
#: (`registry.register_upload_detector`) stays quiet.
NAME = "fastapi"

#: The class FastAPI hands an upload to, as spelled in an annotation.
UPLOAD_FILE = "UploadFile"

#: The containers a multi-file annotation may be built with. `List` is
#: `list`'s pre-3.9 spelling and the rest are the iterable shapes FastAPI
#: accepts for a `files: Sequence[UploadFile]`-style field; `dict`,
#: `Union` and `Optional` are deliberately absent (see the module
#: docstring).
UPLOAD_CONTAINERS = frozenset({
    "list", "List", "Sequence", "Iterable",
    "tuple", "Tuple", "set", "Set", "frozenset", "FrozenSet",
})

#: `File(...)` — the marker that makes a parameter a file field. The
#: annotation FastAPI documents next to it is `bytes`.
FILE_MARKER = "File"

#: `Form(...)` — the marker that makes a parameter a *form* field, and
#: therefore not an upload, whatever its annotation.
FORM_MARKER = "Form"

#: The one annotation `File(...)` is documented with. An absent
#: annotation is the other accepted spelling (FastAPI infers it).
BYTES = "bytes"

#: The decorator attributes `entry_points._extract_route` records a route
#: for — `patterns.HTTP_METHODS` and `patterns.WEBSOCKET_METHOD`,
#: restated rather than imported: `store/` reads no pattern table (the
#: same rule `families.fs` follows for `_has_write_mode`), and this is a
#: membership test, not logic that drifts.
ROUTE_ATTRIBUTES = frozenset({
    "get", "post", "put", "delete", "patch", "options", "head",
    "websocket",
})


class FastAPIUploadDetector(UploadDetector):
    """A FastAPI route handler whose parameters accept an upload."""

    name = NAME

    def detect(
        self,
        tree: ast.Module,
        file_path: str,
        entry_points: list[Node],
    ) -> list[UploadSite]:
        """One site per route handler of `file_path` that accepts a file.

        `entry_points` are the file's entry-point nodes, so a site names
        the node the writer's BFS starts from and the edge's source is
        the same object. A node with no `handler` attribute cannot be
        matched by name and is skipped: a CLI or Streamlit entry is the
        module, not a function, and `entry_points.EntryPointStage`
        records no handler for it.

        Every function of the module is a candidate, class methods
        included — the route check is the name, and a method's name is
        as unique as a function's — so the signature is what decides. A
        name two nodes share yields one site per node, because each node
        is a route the scan found; the writer resolves the same stores
        either way.
        """
        handlers = _nodes_by_handler(entry_points)
        if not handlers:
            return []
        sites: list[UploadSite] = []
        for func in _route_functions(tree):
            nodes = handlers.get(func.name)
            if not nodes or not _accepts_upload(func):
                continue
            sites.extend(
                UploadSite(
                    entry_point=node,
                    file_path=file_path,
                    line=func.lineno,
                    framework=self.name,
                )
                for node in nodes
            )
        return sites


# ------------------------------------------------------------------
# The module's functions
# ------------------------------------------------------------------

def _nodes_by_handler(entry_points: list[Node]) -> dict[str, list[Node]]:
    """`handler` → the entry-point nodes naming it, in the order given.

    A node with no `handler` attribute — a CLI or Streamlit entry — is
    not a function name, so it cannot match one and is left out.
    """
    index: dict[str, list[Node]] = {}
    for node in entry_points:
        handler = node.attributes.get("handler")
        if not isinstance(handler, str) or not handler:
            continue
        index.setdefault(handler, []).append(node)
    return index


def _route_functions(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The module's route-shaped functions, in source order.

    `ast.walk` is breadth-first, so the list is sorted by `lineno` before
    it is filtered: source order is what makes the detector's answer a
    property of the file and not of a walk.
    """
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    found.sort(key=lambda node: node.lineno)
    return [node for node in found if _is_route(node)]


def _is_route(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function carries a route decorator.

    The confirmation and not the decision: `entry_points` is what makes a
    route a route, and this only refuses to claim a function the scan
    never recorded as one (the module docstring states the two
    conditions).
    """
    return any(
        _decorator_name(decorator) in ROUTE_ATTRIBUTES
        for decorator in func.decorator_list
    )


def _decorator_name(decorator: ast.expr) -> str | None:
    """The last segment of a decorator's name (`langchain._decorator_name`).

    `@app.post("/upload")` is an `ast.Call` around an `ast.Attribute` and
    `@post` is an `ast.Name`: both end in the method's own name.
    """
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    return _last_segment(decorator)


# ------------------------------------------------------------------
# One parameter
# ------------------------------------------------------------------

def _accepts_upload(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if any of the handler's parameters is an upload.

    One parameter is enough: FastAPI reads every parameter it is handed,
    so a handler with a file field accepts an upload.
    """
    return any(
        _is_upload_parameter(argument, default)
        for argument, default in _parameters(func)
    )


def _parameters(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[ast.arg, ast.expr | None]]:
    """Every named parameter with its default, positional then keyword-only.

    Positional-only parameters are included: they are positional
    parameters like `args`, and `def upload(file: UploadFile, /)` accepts
    the same upload as the same signature without the `/`. `*args` and
    `**kwargs` are not, because FastAPI does not read an upload through
    them.

    Defaults are aligned the way the AST stores them — the last N of the
    positional list, one per keyword-only entry, `None` where there is no
    default — because a parameter cannot be told from one with
    `File(...)` without its default.
    """
    args = func.args
    positional = [*args.posonlyargs, *args.args]
    padded: list[ast.expr | None] = [None] * (
        len(positional) - len(args.defaults)
    ) + list(args.defaults)
    pairs: list[tuple[ast.arg, ast.expr | None]] = list(
        zip(positional, padded)
    )
    pairs.extend(zip(args.kwonlyargs, args.kw_defaults))
    return pairs


def _is_upload_parameter(argument: ast.arg, default: ast.expr | None) -> bool:
    """True if one parameter accepts an upload (the module's three shapes).

    The `Form(...)` check comes first, because it overrides the
    annotation: `file: UploadFile = Form(...)` is a form field with a
    file-typed annotation, and the marker is what FastAPI reads. That is
    the brief's rule, and the reason this is a refusal and not a
    fallback.
    """
    if _marker(default) == FORM_MARKER:
        return False
    if _is_upload_file(argument.annotation):
        return True
    if _marker(default) != FILE_MARKER:
        return False
    return argument.annotation is None or (
        _last_segment(argument.annotation) == BYTES
    )


def _is_upload_file(annotation: ast.expr | None) -> bool:
    """True if an annotation is `UploadFile` or a container of one.

    `UploadFile` and `fastapi.UploadFile` are read by their last segment
    (an alias is not followed — the module docstring's first limit), and
    a subscript counts only when its base is one of the containers
    FastAPI accepts for a multi-file field *and* its slice is one of the
    two spellings of `UploadFile`.
    """
    if _last_segment(annotation) == UPLOAD_FILE:
        return True
    if not isinstance(annotation, ast.Subscript):
        return False
    if _last_segment(annotation.value) not in UPLOAD_CONTAINERS:
        return False
    return _last_segment(annotation.slice) == UPLOAD_FILE


def _marker(default: ast.expr | None) -> str | None:
    """The `File`/`Form` marker a default carries, or None.

    `File(...)` and `fastapi.File(...)` both end in the marker's name; a
    default that is not a call carries none, because FastAPI requires the
    marker to be called (`file: bytes = File` is the class itself).
    """
    if not isinstance(default, ast.Call):
        return None
    return _last_segment(default.func)


def _last_segment(node: ast.expr | None) -> str | None:
    """The last dotted segment a name or attribute is spelled with.

    `UploadFile` is a `Name` and `fastapi.UploadFile` an `Attribute`;
    anything else — a call, a subscript, a literal — has no name segment,
    and `None` is the honest answer to a question about a name.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


__all__ = [
    "FastAPIUploadDetector",
    "NAME",
]
