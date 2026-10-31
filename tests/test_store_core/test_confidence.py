"""§8's signals and their combination (B.3b-core, acceptance C3).

ADR-0021 §8 fixes three signals — family match (a gate), literal match,
code context — a base of 0.3, a product, and a cap. The constants are a
policy choice the ADR states, so they are pinned here as *numbers*: a
re-tune changes both the ADR and this file, and never one of them.

The base is also §9's floor, and the cap is the same number the edge
repository stores behind, so both are pinned against their other copies.
"""

from __future__ import annotations

import ast

import pytest

from aicorellator.graph import query, repositories
from aicorellator.store import confidence
from aicorellator.store.families.base import (
    ModuleFacts,
    ReadSite,
    WriteSite,
)


def _call() -> ast.Call:
    node = ast.parse("write('/uploads/a')").body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    return node.value


def _write(
    file_path: str = "app/writer.py",
    hint: str | None = "docs",
    imports: list[str] | None = None,
) -> WriteSite:
    facts = (
        ModuleFacts(file_path=file_path, local_imports=frozenset(imports))
        if imports is not None
        else None
    )
    return WriteSite(
        call_site=_call(), file_path=file_path, line=3,
        identity_hint=hint, facts=facts,
    )


def _read(
    file_path: str = "app/reader.py",
    hint: str | None = "docs",
    imports: list[str] | None = None,
) -> ReadSite:
    facts = (
        ModuleFacts(file_path=file_path, local_imports=frozenset(imports))
        if imports is not None
        else None
    )
    return ReadSite(
        call_site=_call(), file_path=file_path, line=9,
        identity_hint=hint, facts=facts,
    )


# ============================================================
# The constants
# ============================================================

def test_the_constants_are_the_ones_in_the_adr() -> None:
    assert confidence.BASE == 0.3
    assert confidence.LITERAL_EQUAL == 0.6
    assert confidence.LITERAL_ONE_SIDE == 0.2
    assert confidence.CODE_CONTEXT_SAME_MODULE == 0.2
    assert confidence.CODE_CONTEXT_SHARED_WRAPPER == 0.3
    assert confidence.CODE_CONTEXT_SHARED_IMPORT == 0.1
    assert confidence.IDENTITY_CONFIDENCE_CAP == 0.99


def test_the_prefix_weight_is_the_familys_row_not_the_adrs() -> None:
    """§8's table has equal / one-side / no-link; B.3b-fs adds a fourth.

    Prefix containment — a writer naming a directory, a reader a file
    inside it — is the `fs` family's motivating case, and the brief that
    specifies the family puts the weight here, beside the other literal
    weights, so the three stay comparable when the ADR's table is
    re-tuned. It is a signal, not a threshold: 0.3, the same as the
    shared wrapper, and still under the 0.7 the default asks for.
    """
    assert confidence.LITERAL_PREFIX == 0.3


def test_the_base_is_the_joins_floor() -> None:
    """§9: a link below the base is no link, and the base is 0.3.

    The resolver never emits below the base — signals only add — so this
    is the invariant that keeps `find_chain_groups`'s floor from
    silently dropping links the resolver thought it had earned.
    """
    assert confidence.BASE == query.WEAK_JOIN_FLOOR


def test_the_cap_matches_the_repositorys_cap() -> None:
    """Two copies of 0.99: the signal's ceiling and the stored one."""
    assert confidence.IDENTITY_CONFIDENCE_CAP == (
        repositories.edges.IDENTITY_CONFIDENCE_CAP
    )


# ============================================================
# literal_match (§8, signal 2)
# ============================================================

def test_two_equal_literals_score_the_full_weight() -> None:
    assert confidence.literal_match("docs", "docs") == 0.6


def test_one_hint_containing_the_other_scores_the_prefix_weight() -> None:
    """§8's extension (B.3b-fs) — a directory and a file inside it.

    Containment is checked in both directions, because either side may
    be the one that names the directory: a writer with
    `'/uploads/' + name` and a reader with `'/uploads/report.txt'` are
    the same store however they are ordered.
    """
    assert confidence.literal_match("/uploads/", "/uploads/x") == 0.3
    assert confidence.literal_match("/uploads/x", "/uploads/") == 0.3


def test_a_missing_trailing_slash_still_contains() -> None:
    """`'/uploads'` and `'/uploads/'` are two hints, and they match.

    Nothing is normalised, so the two spellings are not equal — but
    string containment is one-sided and `'/uploads/'.startswith(
    '/uploads')` is true, so the pair scores the prefix weight rather
    than `None`. A family that wants the two spellings to be *one
    store* has to normalise before it reports; `literal_match` compares
    the hints as written and says what it can.
    """
    assert confidence.literal_match("/uploads", "/uploads/") == 0.3
    assert confidence.literal_match("/uploads/", "/uploads") == 0.3
    # And the container case, which is the row's reason to exist.
    assert confidence.literal_match("/uploads", "/uploads/x") == 0.3


def test_one_side_with_a_literal_scores_the_partial_weight() -> None:
    """The unresolved side may point at the store; the AST does not know."""
    assert confidence.literal_match("docs", None) == 0.2
    assert confidence.literal_match(None, "docs") == 0.2


def test_a_prefix_is_stronger_than_one_side_and_weaker_than_equality() -> (
    None
):
    """The three weights are ordered, which is what makes them a scale."""
    assert (
        confidence.LITERAL_ONE_SIDE
        < confidence.LITERAL_PREFIX
        < confidence.LITERAL_EQUAL
    )


def test_different_literals_are_no_link() -> None:
    """Two names, two stores — pairing them would join unrelated places."""
    assert confidence.literal_match("docs", "other") is None


def test_no_literal_on_either_side_is_no_link() -> None:
    """§5: not evidence of a shared store, so no store at all."""
    assert confidence.literal_match(None, None) is None


# ============================================================
# combine_signals (§8, the combination)
# ============================================================

def test_the_base_alone_is_the_base() -> None:
    assert confidence.combine_signals(0.3, []) == pytest.approx(0.3)


def test_a_signal_multiplies_the_complement() -> None:
    """`1 − (1−0.3)(1−0.6)` — a signal strengthens, never averages."""
    assert confidence.combine_signals(0.3, [0.6]) == pytest.approx(0.72)


def test_signals_multiply_together() -> None:
    """Two independent signals: `1 − 0.7 × 0.4 × 0.8 ≈ 0.776`."""
    assert confidence.combine_signals(0.3, [0.6, 0.2]) == pytest.approx(0.776)


def test_a_named_store_is_just_above_the_default_threshold() -> None:
    """What §9's 0.7 means: the default enumeration is the literal match.

    Equal literals score 0.72 with no code context. Everything weaker —
    a prefix at 0.51, one side unresolved at 0.44, code context alone at
    0.44 — is in the weak band, which is what makes the default a
    selection.
    """
    equal = confidence.combine_signals(0.3, [confidence.LITERAL_EQUAL])
    prefix = confidence.combine_signals(0.3, [confidence.LITERAL_PREFIX])
    one_side = confidence.combine_signals(0.3, [confidence.LITERAL_ONE_SIDE])
    context = confidence.combine_signals(
        0.3, [confidence.CODE_CONTEXT_SAME_MODULE]
    )
    assert equal >= 0.7
    assert 0.3 <= prefix < 0.7
    assert 0.3 <= one_side < 0.7
    assert 0.3 <= context < 0.7


def test_the_cap_short_of_certainty() -> None:
    """A signal of 1 would be certainty; the cap makes it 0.99."""
    assert confidence.combine_signals(0.3, [1.0]) == 0.99


def test_nothing_short_of_an_exact_literal_reaches_the_default() -> None:
    """The bound that makes the literal decisive (§8, §9).

    Two numbers, and the difference between them is the point:

      - a literal a family recognized as *the same store* — prefix
        containment — plus the strongest code-context signal is
        `1 − 0.7 × 0.7 × 0.7 = 0.657`, still under 0.7;
      - what the *resolver* can reach without an exact literal is
        `0.608` (`1 − 0.7 × 0.8 × 0.7`): the one-side literal plus the
        shared wrapper. Since B.3b-fs-followup prefix containment is
        reachable through the resolver too — `resolver._attached_keys`
        attaches a site to a store whose literal contains its own or is
        contained by it — so `0.657` is reachable as well; both are
        still under 0.7, which is the point.

    Either way a pair that did not name the same store cannot enter the
    default enumeration however much of the code agrees about where it
    lives.
    """
    strongest = confidence.combine_signals(
        0.3,
        [confidence.LITERAL_PREFIX, confidence.CODE_CONTEXT_SHARED_WRAPPER],
    )
    reached = confidence.combine_signals(
        0.3,
        [confidence.LITERAL_ONE_SIDE, confidence.CODE_CONTEXT_SHARED_WRAPPER],
    )
    assert round(strongest, 4) == 0.657
    assert round(reached, 4) == 0.608
    assert strongest < 0.7
    assert reached < strongest


def test_a_zero_signal_is_a_no_op() -> None:
    assert confidence.combine_signals(0.3, [0.6, 0.0]) == pytest.approx(0.72)


# ============================================================
# code_context (§8, signal 3)
# ============================================================

def test_same_module_scores_its_weight() -> None:
    assert confidence.code_context(
        _write("app/handler.py"), _read("app/handler.py")
    ) == 0.2


def test_a_shared_local_import_scores_the_wrapper_weight() -> None:
    """Both files import the module the store is reached through."""
    assert confidence.code_context(
        _write("app/routes.py", imports=["app/storage.py"]),
        _read("app/jobs.py", imports=["app/storage.py"]),
    ) == 0.3


def test_one_file_importing_the_other_scores_the_weakest_weight() -> None:
    assert confidence.code_context(
        _write("app/routes.py", imports=["app/handlers.py"]),
        _read("app/handlers.py", imports=[]),
    ) == 0.1


def test_nothing_shared_scores_zero() -> None:
    assert confidence.code_context(
        _write("app/routes.py", imports=["app/storage.py"]),
        _read("app/jobs.py", imports=["app/other.py"]),
    ) == 0.0


def test_the_same_module_rule_wins_over_the_others() -> None:
    """The rules are ranked alternatives, in the ADR's order."""
    same_module = confidence.code_context(
        _write("app/handlers.py", imports=["app/storage.py"]),
        _read("app/handlers.py", imports=["app/storage.py"]),
    )
    assert same_module == 0.2


def test_a_pair_with_no_facts_scores_zero_across_files() -> None:
    """A hand-built site carries no imports; the resolver fills them.

    Nothing is invented from what is not there: no facts, no shared
    wrapper and no shared import path, so only the same-module rule
    could have applied — and these two are in different modules.
    """
    assert confidence.code_context(
        _write("app/routes.py"), _read("app/jobs.py")
    ) == 0.0


# ============================================================
# module_facts — what the code-context signal reads
# ============================================================

PATHS = {
    "app": "app/__init__.py",
    "app.storage": "app/storage.py",
    "app.handlers": "app/handlers.py",
    "app.jobs": "app/jobs.py",
    "app.other": "app/other.py",
    "other.client": "other/client.py",
}


def _facts(source: str, file_path: str = "app/routes.py") -> set[str]:
    return set(
        confidence.module_facts(
            file_path, ast.parse(source), PATHS
        ).local_imports
    )


def test_absolute_import_resolves_to_a_manifest_path() -> None:
    assert _facts("import app.storage") == {"app/storage.py"}


def test_from_import_offers_the_module_and_the_names() -> None:
    """`storage` may be a submodule or an attribute; both are offered."""
    assert _facts("from app import storage") == {
        "app/__init__.py", "app/storage.py",
    }


def test_from_module_import_names_offers_both_spellings() -> None:
    assert _facts("from app import handlers, jobs") == {
        "app/__init__.py", "app/handlers.py", "app/jobs.py",
    }


def test_a_relative_import_derives_its_package_from_the_file() -> None:
    """The AST records the level; the file's directory supplies the rest."""
    assert _facts(
        "from .storage import client", "app/routes.py"
    ) == {"app/storage.py"}
    assert _facts(
        "from ..other import client", "app/api/routes.py"
    ) == {"app/other.py"}


def test_a_relative_import_in_a_package_init() -> None:
    assert _facts("from . import jobs", "app/__init__.py") == {
        "app/__init__.py", "app/jobs.py",
    }


def test_a_name_the_scan_did_not_discover_is_not_local() -> None:
    """`os` and a third-party package are not a shared wrapper."""
    assert _facts(
        "import os\nimport requests\nfrom yaml import safe_load"
    ) == set()


def test_a_docstring_is_not_an_import() -> None:
    assert _facts('"""import app.storage"""') == set()
