"""The detector registers itself (B.3b-user_upload-django, R1–R3).

A detector is a module plus a line in `store/context/upload/detectors/`,
the rule `store.families` follows for families: importing the package
runs `register_upload_detector`, and nothing else has to name it. These
tests pin that, and pin that the two registries stay apart — a family is
a store *technology*, a detector is a *framework* (ADR-0021 §1, revised).

`tests/test_store_upload_core/conftest.py` empties `UPLOAD_DETECTORS` for
every test in *that* directory; this one runs with the tree's own
registry, which is what a scan sees.
"""

from __future__ import annotations

import ast
import importlib

from aicorellator.graph.models import Node
from aicorellator.store import registry as family_registry
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload import detectors as detectors_package
from aicorellator.store.context.upload.context import UserUploadContext
from aicorellator.store.context.upload.detectors import django as detector
from aicorellator.store.context.upload.detectors.django import (
    DjangoUploadDetector,
)

FILE = "app/views.py"

SOURCE = '''\
"""A Django view that accepts an upload."""
from router import router


@router.post("/upload")
def upload(request):
    return request.FILES["file"]
'''


def _node(handler: str = "upload") -> Node:
    """The entry-point node the `entry_points` stage would build."""
    return Node(
        id=f"entry_point:{handler}", kind="entry_point",
        name=f"POST /{handler}", host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": 6, "method": "POST",
            "path": f"/{handler}", "handler": handler,
        },
    )


def _streamlit_node() -> Node:
    """A module-level entry node, which no function name can match."""
    return Node(
        id="entry_point:file_uploader", kind="entry_point",
        name=f"Streamlit:{FILE}:file_uploader", host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": 1, "method": "STREAMLIT",
            "entry_kind": "streamlit", "path": "file_uploader",
        },
    )


# ============================================================
# R1 — importing the package is the registration
# ============================================================

def test_importing_the_detectors_package_registers_django(
    monkeypatch,
) -> None:
    """R1: empty the registry, re-import the package, all four are there.

    A reload is the only way to observe the import in a process that has
    already imported the package: the registration is a statement in
    `detectors/__init__.py`, so re-executing that module is exactly the
    event a fresh interpreter performs.
    """
    monkeypatch.setattr(registry, "UPLOAD_DETECTORS", {})
    assert registry.all_names() == []

    importlib.reload(detectors_package)

    assert registry.all_names() == [
        "django", "fastapi", "flask", "streamlit",
    ]
    assert registry.UPLOAD_DETECTORS["django"] is DjangoUploadDetector
    assert registry.UPLOAD_DETECTORS["fastapi"] is not None
    assert registry.UPLOAD_DETECTORS["streamlit"] is not None


def test_reimporting_twice_is_not_an_error() -> None:
    """The registry allows the same class under the same name."""
    importlib.reload(detectors_package)
    importlib.reload(detectors_package)
    assert registry.UPLOAD_DETECTORS["django"] is DjangoUploadDetector


def test_the_tree_ships_the_detector() -> None:
    """No caller registered it: the import of `store` already did."""
    assert registry.UPLOAD_DETECTORS["django"] is DjangoUploadDetector


# ============================================================
# R2 — the registry resolves it
# ============================================================

def test_resolve_returns_an_instance() -> None:
    """R2: `resolve(["django"])` is one instance, not the class."""
    resolved = registry.resolve(["django"])

    assert len(resolved) == 1
    assert isinstance(resolved[0], DjangoUploadDetector)
    assert resolved[0].name == "django"


def test_resolving_all_gives_every_detector_in_registration_order() -> None:
    """R2 — a scan's default is the whole registry, in order.

    `flask` is registered last, so it resolves last, and `django` third
    (B.3b-user_upload-django): a scan runs the detectors in the order
    `detectors/__init__.py` names them, and the context's dedup is what
    decides between two of them.
    """
    resolved = registry.resolve(None)

    assert [det.name for det in resolved] == [
        "fastapi", "streamlit", "django", "flask",
    ]


def test_the_class_declares_the_key() -> None:
    """The registry key and the class's `name` are one string."""
    assert DjangoUploadDetector.name == "django"
    assert detector.NAME == "django"
    assert DjangoUploadDetector.name == detector.NAME


# ============================================================
# R3 — `all_names`, and the families registry untouched
# ============================================================

def test_all_names_includes_django() -> None:
    """R3: the name a config asks for is the name in the list."""
    assert "django" in registry.all_names()
    assert registry.all_names() == [
        "django", "fastapi", "flask", "streamlit",
    ]


def test_the_families_registry_is_untouched() -> None:
    """R3: a framework is not a store technology (ADR-0021 §1)."""
    assert "django" not in family_registry.FAMILIES
    assert "django" not in family_registry.all_names()
    assert registry.UPLOAD_DETECTORS is not family_registry.FAMILIES


def test_the_upload_context_reads_the_registry_at_identify_time() -> None:
    """The context's default is the registry, and an empty list stays empty.

    `StoreResolverStage` builds its context once, at import, while
    `STAGES` is built — so a context that captured the registry at
    construction would never see a detector registered later in the
    process. `detectors=[]` is the explicit "no detectors" answer.
    """
    tree = ast.parse(SOURCE)

    assert [
        site.framework for site in UserUploadContext().identify(
            tree, FILE, [_node()]
        )
    ] == ["django"]
    assert UserUploadContext(detectors=[]).identify(
        tree, FILE, [_node()]
    ) == []


def test_the_context_identifies_one_framework_per_site() -> None:
    """Two nodes, two detectors, one site each: a site names its own node.

    `django` names the route node and `streamlit` the module-level one —
    and the Streamlit detector does not read the tree, so it claims the
    node it was handed whatever the file holds (its module docstring).
    The context deduplicates by `(entry_point.id, framework)`, so the two
    coexist: one site per framework, in registration order.
    """
    sites = UserUploadContext().identify(
        ast.parse(SOURCE), FILE, [_node(), _streamlit_node()],
    )

    assert [site.framework for site in sites] == ["streamlit", "django"]
    assert [site.entry_point.id for site in sites] == [
        "entry_point:file_uploader", "entry_point:upload",
    ]
