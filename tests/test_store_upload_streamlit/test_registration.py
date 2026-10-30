"""The Streamlit detector registers itself (R1–R3).

A detector is a module plus a line in `store/context/upload/detectors/`,
the rule `store.families` follows for families: importing the package
runs `register_upload_detector`, and nothing else has to name it. These
tests pin that for the second detector, and pin that the two detectors
coexist — `fastapi` first, `streamlit` second, one instance each, and the
*family* registry untouched (`streamlit` is a framework, not a store
technology: ADR-0021 §1, revised).

`tests/test_store_upload_core/conftest.py` empties `UPLOAD_DETECTORS` for
every test in *that* directory; this one runs with the tree's own
registry, which is what a scan sees.
"""

from __future__ import annotations

import ast
import importlib

from aicorellator.graph.models import Node
from aicorellator.store import registry as family_registry
from aicorellator.store.context.upload import detectors as detectors_package
from aicorellator.store.context.upload import registry
from aicorellator.store.context.upload.context import UserUploadContext
from aicorellator.store.context.upload.detectors import streamlit as detector
from aicorellator.store.context.upload.detectors.fastapi import (
    FastAPIUploadDetector,
)
from aicorellator.store.context.upload.detectors.streamlit import (
    StreamlitUploadDetector,
)

FILE = "app/streamlit_app.py"

SOURCE = '''\
import streamlit as st
from fastapi import FastAPI, UploadFile

app = FastAPI()

uploaded = st.file_uploader("Upload a report")


@app.post("/upload")
async def upload_report(file: UploadFile):
    return {}
'''


def _streamlit_node() -> Node:
    """The entry-point node the `entry_points` stage would build."""
    return Node(
        id="entry_point:streamlit", kind="entry_point",
        name=f"Streamlit:{FILE}:file_uploader", host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": 6, "method": "STREAMLIT",
            "entry_kind": "streamlit", "path": "file_uploader",
        },
    )


def _fastapi_node() -> Node:
    """The route node the `entry_points` stage would build."""
    return Node(
        id="entry_point:fastapi", kind="entry_point",
        name="POST /upload", host_id="localhost",
        attributes={
            "source_file": FILE, "line_start": 10, "method": "POST",
            "path": "/upload", "handler": "upload_report",
        },
    )


# ============================================================
# R1 — importing the package is the registration
# ============================================================

def test_importing_the_detectors_package_registers_every_detector(
    monkeypatch,
) -> None:
    """R1: empty the registry, re-import the package, all three are there.

    A reload is the only way to observe the import in a process that has
    already imported the package: the registration is a statement in
    `detectors/__init__.py`, so re-executing that module is exactly the
    event a fresh interpreter performs.
    """
    monkeypatch.setattr(registry, "UPLOAD_DETECTORS", {})
    assert registry.all_names() == []

    importlib.reload(detectors_package)

    assert registry.all_names() == ["django", "fastapi", "streamlit"]
    assert registry.UPLOAD_DETECTORS["streamlit"] is StreamlitUploadDetector
    assert registry.UPLOAD_DETECTORS["fastapi"] is FastAPIUploadDetector


def test_reimporting_twice_is_not_an_error() -> None:
    """The registry allows the same class under the same name."""
    importlib.reload(detectors_package)
    importlib.reload(detectors_package)
    assert registry.UPLOAD_DETECTORS["streamlit"] is StreamlitUploadDetector


def test_the_tree_ships_the_detector() -> None:
    """No caller registered it: the import of `store` already did."""
    assert registry.UPLOAD_DETECTORS["streamlit"] is StreamlitUploadDetector


# ============================================================
# R2 — the registry resolves it
# ============================================================

def test_resolve_returns_an_instance() -> None:
    """R2: `resolve(["streamlit"])` is one instance, not the class."""
    resolved = registry.resolve(["streamlit"])

    assert len(resolved) == 1
    assert isinstance(resolved[0], StreamlitUploadDetector)
    assert resolved[0].name == "streamlit"


def test_resolving_all_gives_every_detector_in_registration_order() -> None:
    """R2 — a scan's default is the whole registry, in order."""
    resolved = registry.resolve(None)

    assert [det.name for det in resolved] == [
        "fastapi", "streamlit", "django",
    ]


def test_the_class_declares_the_key() -> None:
    """The registry key and the class's `name` are one string."""
    assert StreamlitUploadDetector.name == "streamlit"
    assert detector.NAME == "streamlit"
    assert StreamlitUploadDetector.name == detector.NAME


# ============================================================
# R3 — `all_names`, and the families registry untouched
# ============================================================

def test_all_names_includes_streamlit() -> None:
    """R3: the name a config asks for is the name in the list."""
    assert "streamlit" in registry.all_names()
    assert registry.all_names() == ["django", "fastapi", "streamlit"]


def test_the_families_registry_is_untouched() -> None:
    """R3: a framework is not a store technology (ADR-0021 §1)."""
    assert "streamlit" not in family_registry.FAMILIES
    assert "streamlit" not in family_registry.all_names()
    assert registry.UPLOAD_DETECTORS is not family_registry.FAMILIES


def test_the_context_identifies_both_frameworks_in_one_file() -> None:
    """R2's end: two detectors, one file, two sites, both frameworks.

    The context asks every registered detector about the file and
    deduplicates by `(entry_point.id, framework)`, so a file that holds
    both a Streamlit upload and a FastAPI route gets one site per
    detector — two different nodes, and two different writers.
    """
    sites = UserUploadContext().identify(
        ast.parse(SOURCE), FILE, [_streamlit_node(), _fastapi_node()],
    )

    assert [site.framework for site in sites] == ["fastapi", "streamlit"]
    assert [site.entry_point.id for site in sites] == [
        "entry_point:fastapi", "entry_point:streamlit",
    ]
