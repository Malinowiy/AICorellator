"""The module-level writer with no store to link to (B.3b-…-module, W4).

The module-level branch finds a write the same way the function-level one
does, so it earns no edge for the same three reasons: the delta holds no
store node for the literal the write names, the write names no store at
all (§5 — a parameter is not an identity), or the store the delta holds
is of another family. §8's gate is the family and the identity is the
literal; a module-level site is not exempt from either.
"""

from __future__ import annotations

from .conftest import LITERAL, MODULE, fs_writes, run_module

# A module body that writes a store named by a parameter: no literal, so
# no store (§5).
PARAMETER_WRITE = (
    'with open(path, "wb") as fh:\n'
    "    fh.write('x')\n"
)

# A function the module body calls, writing a store named by a
# parameter: the write is in the reach, and §5 is the only reason it
# earns no edge.
FUNCTION_PARAMETER_WRITE = (
    "def save(payload, path):\n"
    '    with open(path, "wb") as fh:\n'
    "        fh.write(payload)\n"
    "\n"
    'save(b"x", None)\n'
)

# A module body that writes an f-string: the family reports the prefix
# before the first `{`, which is a literal in its own right.
PREFIX_WRITE = (
    'with open(f"/uploads/{name}", "wb") as fh:\n'
    "    fh.write('x')\n"
)

MODULE_BODY = (
    f'with open("{LITERAL}", "wb") as fh:\n'
    "    fh.write('x')\n"
)


def test_a_module_body_write_with_no_store_node_is_no_edge(project) -> None:
    """The store node is what the edge points at; without it, no edge."""
    _, edges = run_module(project, {MODULE: MODULE_BODY}, stores=())

    assert edges == []


def test_a_module_body_write_naming_no_store_is_no_edge(project) -> None:
    """§5 — a parameter names nothing, and a store needs a literal."""
    built, edges = run_module(project, {MODULE: PARAMETER_WRITE})

    assert [write.identity_hint for write in fs_writes(
        built, {MODULE: PARAMETER_WRITE}
    )] == [None]
    assert edges == []


def test_a_function_write_naming_no_store_is_no_edge(project) -> None:
    """The same, through a function the module body calls.

    The write **is** in the reach — the body calls `save` — so §5 is the
    only reason it earns no edge: the family reported the site with
    `identity_hint=None`, and a store needs a literal.
    """
    built, edges = run_module(
        project, {MODULE: FUNCTION_PARAMETER_WRITE},
        call_graph={"save": set()},
    )

    assert [write.identity_hint for write in fs_writes(
        built, {MODULE: FUNCTION_PARAMETER_WRITE}
    )] == [None]
    assert edges == []


def test_an_fstring_prefix_names_its_own_store_and_not_the_literal(
    project,
) -> None:
    """`f"/uploads/{name}"` names `/uploads/` — a different store.

    The lookup is exact equality, so a module body writing into
    `/uploads/report.txt` is not the writer of `/uploads/`; the other
    direction of the same rule is pinned with a store the prefix does
    name.
    """
    _, unmatched = run_module(project, {MODULE: PREFIX_WRITE})
    assert unmatched == []

    _, matched = run_module(
        project, {MODULE: PREFIX_WRITE}, stores=("/uploads/",),
    )
    assert len(matched) == 1


def test_a_store_of_another_family_is_not_the_modules_write(project) -> None:
    """The lookup is `(family, literal)`: an `s3` store is a different one."""
    _, edges = run_module(
        project, {MODULE: MODULE_BODY}, store_family="s3",
    )

    assert edges == []
