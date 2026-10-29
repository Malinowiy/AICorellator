"""The `fs` family — file-system write and read call sites (ADR-0021 §12).

A store in this family is a path. `open('/uploads/report.txt', 'wb')`
writes the store the literal names and `open('/uploads/report.txt',
'rb')` reads it; the family reports the two call sites and the literal
they carry, and nothing else. `StoreResolver` pairs them (§8), computes
the confidence, and builds the node and the edges.

The shapes, and only these:

  - `open(path, mode)` — the builtin, `builtins.open` included;
  - `aiofiles.open(path, mode)`;
  - `Path(path).write_text(...)` / `.write_bytes(...)`;
  - `Path(path).read_text(...)` / `.read_bytes(...)`;
  - `Path(path).open(mode)`.

`mode` decides the direction, by the rule the sink extractor already
applies (`capability_extractor._has_write_mode`, copied rather than
imported — `store/` does not depend on `parsing/`): `w`, `a`, `x` or
`+` anywhere in it makes the call a write, and a mode with none of `w`,
`a`, `x` makes it a read. An absent mode is a read, because that is
`open`'s default; `'r+'` is **both**, one call site claiming both roles
(and a group is two components, so one call site is never a group on its
own). A mode that is not a string literal is neither: the AST cannot say
which way the call goes, and ADR-0016's rule is to stay silent.

**Not this family**, deliberately: `os.remove`, `shutil.move`,
`shutil.copy`, `shutil.rmtree`, `os.listdir`, `Path.unlink`, `os.rename`
and the rest of ADR-0009's `writes_filesystem` / `reads_filesystem`
call sites. They carry no data from one place to another that this model
can pair. `open(..., 'wb')` is in both worlds and they are *different
facts about the same call*: a sink capability
(`patterns.WRITES_FILESYSTEM_RULE`, read by the classifier) and a store
operation (read by this family). Nothing here touches the sink side.

The literal, which is §8's identity hint:

  - a string constant is the whole hint — `'/uploads/report.txt'`;
  - an expression built from one takes its **left** side, so a prefix is
    the hint: `'/uploads/' + name` names the directory, and
    `Path('/uploads') / 'x'` names `/uploads` (no trailing slash is
    invented and none is removed — `confidence.literal_match` compares
    the literals as written);
  - an f-string takes the constant part before the first `{...}`:
    `f'/uploads/{name}'` names `'/uploads/'`, and `f'{base}/x'` names
    nothing;
  - anything else — a parameter, an attribute, a call — names nothing,
    and `None` is the honest answer. §5 turns it into "no store" rather
    than into a guessed one.

What the family does not do, and why:

  - **It does not follow variables.** `p = Path('/uploads');
    p.write_text(data)` has a `Name` where a `Path(...)` call would be,
    so there is no site at all — no assignment chains (ADR-0016).
  - **It does not follow aliases.** `import aiofiles as aio;
    aio.open(path, 'wb')` is invisible for the same reason.
  - **It requires a `Path(...)` receiver.** A `write_text` method on
    some other class is not `pathlib.Path`, so only a receiver built
    from a `Path(...)` / `pathlib.Path(...)` call matches. A `Path` held
    in a variable is the same limitation as above. A receiver built by
    dividing one — `(Path('/uploads') / name).write_text(data)` —
    matches, and names the directory the chain started from.
  - **It does not decide the role of a read.** `ReadSite.source_type`
    stays `None`: a read's semantic role (`user_upload`, `rag_corpus`)
    is the context's business (§1, B.3b-user_upload), and the resolver
    finds the `untrusted_source` a read belongs to by its call site.

The sites come back in source order (line, then column), so a family's
report is a property of the code and not of `ast.walk`'s breadth-first
order.
"""

from __future__ import annotations

import ast

from .base import Context, Family, ReadSite, WriteSite

#: The dotted names `pathlib.Path` is spelled with. `pathlib.Path` and
#: `Path` are the same class; real code uses both spellings.
_PATH_CLASSES = ("Path", "pathlib.Path")

#: The dotted names of the builtin `open`. `os.open` is deliberately
#: absent: it returns a file descriptor, and it is not the builtin.
_OPEN_TARGETS = ("open", "builtins.open")

#: The mode characters that make a call a write, and the ones that
#: disqualify it from being a read (`capability_extractor`'s rule).
_WRITE_MODE_CHARS = ("w", "a", "x", "+")
_READ_MODE_CHARS = ("w", "a", "x")

#: The `Path` methods whose name states their direction.
_PATH_WRITES = ("write_text", "write_bytes")
_PATH_READS = ("read_text", "read_bytes")


class FsFamily(Family):
    """File-system stores: `open` and `Path`, by the literal path.

    Both detectors walk the module once and return the sites they
    recognize. Neither raises — a family that throws is logged and
    skipped by the resolver, and an empty list is the same answer.
    """

    name = "fs"

    def detect_writes(
        self,
        tree: ast.Module,
        file_path: str,
        *,
        context: Context | None = None,
    ) -> list[WriteSite]:
        """The calls in one module that write into a file-system store.

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
        """The calls in one module that read from a file-system store.

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
    the traversal. A call that matches no shape is skipped, and a call
    that matches a shape but neither direction (a mode the AST cannot
    read) is skipped with it: there is no site to report.
    """
    sites: list[tuple[ast.Call, str | None, bool, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        shape = _classify(node)
        if shape is None:
            continue
        path, _mode, write, read = shape
        if not (write or read):
            continue
        sites.append((node, _literal_prefix(path), write, read))
    sites.sort(key=lambda site: (site[0].lineno, site[0].col_offset))
    return sites


def _classify(
    call: ast.Call,
) -> tuple[ast.expr | None, ast.expr | None, bool, bool] | None:
    """What one call is, as `(path, mode, write, read)`, or `None`.

    Three shapes, each with its own argument layout. The builtin `open`
    takes the path first and the mode second, and `aiofiles.open` is the
    same; a `Path(...)` method takes the path from its *receiver*, so
    `Path('/x').open('wb')`'s mode is the **first** argument and
    `Path('/x').write_text(data)` has no mode at all — its name is its
    direction.

    Anything else is not this family's: another module's `open`
    (`os.open`), a `read_text` on a receiver that is not `Path(...)`,
    and every sink call the module docstring lists.
    """
    func = call.func
    dotted = _dotted(func)

    if dotted in _OPEN_TARGETS or _is_aiofiles_open(dotted):
        mode = _argument(call, 1, "mode")
        write, read = _mode_direction(mode)
        return _argument(call, 0, "file"), mode, write, read

    if not isinstance(func, ast.Attribute):
        return None
    path = _receiver_path_expr(call)
    if path is None:
        return None
    if func.attr in _PATH_WRITES:
        return path, None, True, False
    if func.attr in _PATH_READS:
        return path, None, False, True
    if func.attr == "open":
        mode = _argument(call, 0, "mode")
        write, read = _mode_direction(mode)
        return path, mode, write, read
    return None


def _is_aiofiles_open(dotted: str | None) -> bool:
    """True for `aiofiles.open` — the last segment `open`, under it.

    The brief's rule, spelled out: the leftmost segment names the module
    and the last one the call, so `aiofiles.open` and a deeper
    `aiofiles.threadpool.open` both match. `aiofiles` imported under an
    alias is a known false negative (the module docstring).
    """
    if dotted is None:
        return False
    parts = dotted.split(".")
    return parts[0] == "aiofiles" and parts[-1] == "open"


def _receiver_path_expr(call: ast.Call) -> ast.expr | None:
    """The expression naming the path a `Path` method is invoked on.

    `Path('/uploads/x').write_text(...)` names `'/uploads/x'`, and
    `(Path('/uploads') / 'x').write_text(...)` names `'/uploads'`: the
    division adds a segment to a directory, so the directory is what the
    site names (`_literal_prefix` takes the left side of a `BinOp`).
    `None` when the receiver is not built from a `Path(...)` call at all
    — a variable, a `str`, some other class's method — which is the
    known false negative the module docstring lists.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    return _path_expr(func.value)


def _path_expr(expr: ast.expr) -> ast.expr | None:
    """The path argument of the `Path(...)` call an expression names.

    Recurses through `/` (a `BinOp`), so a chain of segments ends at the
    `Path(...)` call that started it. `None` for anything else, which is
    how a receiver that is not a `Path(...)` expression is rejected.
    """
    if isinstance(expr, ast.BinOp):
        return _path_expr(expr.left)
    if not isinstance(expr, ast.Call):
        return None
    if _dotted(expr.func) not in _PATH_CLASSES:
        return None
    return _argument(expr, 0, "file")


def _mode_direction(mode: ast.expr | None) -> tuple[bool, bool]:
    """`(write, read)` for a mode argument — the sink extractor's rule.

    An absent mode is a read, because `'r'` is `open`'s default. A
    string literal is a write when it carries any of `w`, `a`, `x`, `+`
    and a read when it carries none of `w`, `a`, `x` — so `'r+'` and
    `'rb+'` are **both** (the `+` asks for the other half too, and the
    resolver pairs distinct sites, so one call site with two roles is
    not a group on its own), while `'w+'` is a write only. Anything else
    — a parameter, an f-string — is neither: the AST cannot read the
    mode, and a direction invented from it would be a guess.
    """
    if mode is None:
        return False, True
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return False, False
    text = mode.value
    write = any(char in text for char in _WRITE_MODE_CHARS)
    read = not any(char in text for char in _READ_MODE_CHARS)
    return write, read


def _argument(call: ast.Call, index: int, keyword: str) -> ast.expr | None:
    """The `index`-th positional argument, else the `keyword` argument.

    `open`'s two arguments have names (`file`, `mode`), so a call may
    pass either one by keyword; the positional form wins when both are
    present, which is Python's own rule. `Path`'s constructor has no
    such name — `Path(*args)` — so the keyword lookup finds nothing
    there and the positional form is the only one.
    """
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _dotted(node: ast.expr) -> str | None:
    """The dotted name of an expression, or `None`.

    `open` → `"open"`, `aiofiles.open` → `"aiofiles.open"`, and
    `Path('/x').write_text` → `None`, because its receiver is a call and
    not a name. The same walk `capability_extractor._dotted_name` does,
    copied for the reason the mode rule is: `store/` does not import
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


def _literal_prefix(expr: ast.expr | None) -> str | None:
    """The literal a path expression names — §8's identity hint.

    A string constant is the hint. An expression *built from* one takes
    its **left** side, which is the prefix the site names:
    `'/uploads/' + name` names the directory, and `Path('/uploads') /
    'x'` names `/uploads`. No trailing slash is invented and none is
    removed — the literals are compared as written
    (`confidence.literal_match`), so `'/uploads/'` and `'/uploads'` stay
    two different hints and the pair meets on containment rather than on
    equality. An f-string takes the constant part before the first
    `{...}`; when it starts with one, it names nothing. Every other
    expression — a parameter, an attribute, a call — names nothing,
    which is §5's `None`.
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


__all__ = ["FsFamily"]
