"""The detector registers itself (B.3b-user_upload-fastapi, R1–R3).

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
from aicorellator.store.context.upload.detectors import fastapi as detector
from aicorellator.store.context.upload.detectors.fastapi import (
    FastAPIUploadDetector,
)

FILE = "app/main.py"

SOURCE = '''\
from fastapi import FastAPI, UploadFile

app = FastAPI()


@app.post("/upload")
async def upload(file: UploadFile):
    return {}
'''


def _node(handler: str = "upload") -> Node:
    """The entry-point node the `entry_points` stage would build."""
    return Node(
        id=f"entry_point:{handler}", kind="entry_point",
        name=f"POST /{handler}", host_id="localhost",
        attributes={"source_file": FILE, "handler": handler},
    )


# ============================================================
# R1 — importing the package is the registration
# ============================================================

def test_importing_the_detectors_package_registers_fastapi(
    monkeypatch,
) -> None:
    """R1: empty the registry, re-import the package, `fastapi` is there.

    A reload is the only way to observe the import in a process that has
    already imported the package: the registration is a statement in
    `detectors/__init__.py`, so re-executing that module is exactly the
    event a fresh interpreter performs.
    """
    monkeypatch.setattr(registry, "UPLOAD_DETECTORS", {})
    assert registry.all_names() == []

    importlib.reload(detectors_package)

    assert registry.all_names() == ["fastapi"]
    assert registry.UPLOAD_DETECTORS["fastapi"] is FastAPIUploadDetector


def test_reimporting_twice_is_not_an_error() -> None:
    """The registry allows the same class under the same name."""
    importlib.reload(detectors_package)
    importlib.reload(detectors_package)
    assert registry.UPLOAD_DETECTORS["fastapi"] is FastAPIUploadDetector


def test_the_tree_ships_the_detector() -> None:
    """No caller registered it: the import of `store` already did."""
    assert registry.UPLOAD_DETECTORS["fastapi"] is FastAPIUploadDetector


# ============================================================
# R2 — the registry resolves it
# ============================================================

def test_resolve_returns_an_instance() -> None:
    """R2: `resolve(["fastapi"])` is one instance, not the class."""
    resolved = registry.resolve(["fastapi"])
    assert len(resolved) == 1
    assert isinstance(resolved[0], FastAPIUploadDetector)
    assert resolved[0].name == "fastapi"


def test_the_registered_detector_is_the_shipped_one() -> None:
    """The instance a scan gets is the module's class."""
    assert isinstance(
        registry.registered_detectors()[0], FastAPIUploadDetector
    )


def test_the_class_declares_the_key() -> None:
    """The registry key and the class's `name` are one string."""
    assert FastAPIUploadDetector.name == "fastapi"
    assert detector.NAME == "fastapi"
    assert FastAPIUploadDetector.name == detector.NAME


# ============================================================
# R3 — `all_names`, and the families registry untouched
# ============================================================

def test_all_names_includes_fastapi() -> None:
    """R3: the name a config asks for is the name in the list."""
    assert "fastapi" in registry.all_names()


def test_the_families_registry_is_untouched() -> None:
    """R3: a framework is not a store technology (ADR-0021 §1).

    `fastapi` is in the detector registry and in no family registry, and
    the two dicts are different objects — the separation is what keeps
    `resolve_families(["fs"])` from finding a framework.
    """
    assert "fastapi" not in family_registry.FAMILIES
    assert "fastapi" not in family_registry.all_names()
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
    ] == ["fastapi"]
    assert UserUploadContext(detectors=[]).identify(
        tree, FILE, [_node()]
    ) == []
