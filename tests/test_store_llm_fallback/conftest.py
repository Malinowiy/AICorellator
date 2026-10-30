"""Shared builders for the store-identity fallback tests (B.3b-llm-fallback).

Four things every test in this package needs, and none of them should
build by hand:

  - `answer(...)` — a well-formed assistant message. The fallback's input
    is JSON text, so the tests speak JSON text.
  - `FakeLLM` — a scripted `llm_call`, consumed in call order (D.2's, so
    the tests read the same way as the classifier's).
  - `write_site` / `read_site` — a `FamilySite` whose `call_site` is a
    real `ast.Call`, because the prompt's call shape is `ast.unparse`'d
    from it.
  - `case(...)` — a whole runner input set: files on disk (the runner
    parses them for the code window), a delta with the nodes the two ends
    resolve against, upload sites for the scope rule, a config file and a
    cache path, plus a `run()` that fills in a policy.

No test in this package touches the network: `llm_call` is always
injected, and the one test that exercises the HTTP path monkeypatches
`requests.post`.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from aicorellator.graph.models import Node
from aicorellator.ingestion.discovery import FileEntry, FileManifest
from aicorellator.ingestion.target import Target
from aicorellator.parsing.ast_normalizer import GraphDelta
from aicorellator.parsing.entry_points import EntryPointOutput
from aicorellator.parsing.models import TSAOutput
from aicorellator.parsing.normalizer_context import make_context
from aicorellator.scan_config import default_scan_config
from aicorellator.store.context.upload.base import UploadSite
from aicorellator.store.families.base import ReadSite, WriteSite
from aicorellator.store.llm_fallback import FamilySite, run
from aicorellator.store.llm_fallback.cache import FallbackCache


CONFIG_YAML = """\
llm_fallback_model:
  base_url: "http://localhost:1234/v1"
  model: "test/model"
  temperature: 0.0
  max_tokens: 400
  timeout: 120
"""


def answer(hint: str | None, confidence: float = 0.8) -> str:
    """A well-formed assistant message naming (or refusing to name) a store."""
    return json.dumps({
        "identity_hint": hint,
        "confidence": confidence,
        "reasoning": f"the code says {hint!r}",
    })


class FakeLLM:
    """A scripted `llm_call`, consumed in call order.

    `None` entries stand for "no answer" (a timeout, a refused format), and
    an `Exception` entry is raised, so the failure paths are as easy to
    script as the happy one.
    """

    def __init__(self, *responses: str | None | Exception) -> None:
        self.responses: list[str | None | Exception] = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str) -> str | None:
        self.calls.append((system, user))
        if not self.responses:
            return None
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _call(source: str) -> ast.Call:
    """The first `ast.Call` in `source`, for a site's `call_site`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            return node
    raise AssertionError(f"no call in {source!r}")


def write_site(
    *,
    file_path: str = "app/upload.py",
    line: int = 3,
    identity_hint: str | None = None,
    source: str = 'open(path, "wb")',
) -> FamilySite:
    """A `FamilySite` holding one write site (the `fs` family's shape)."""
    return FamilySite(
        family="fs",
        site=WriteSite(
            call_site=_call(source), file_path=file_path, line=line,
            identity_hint=identity_hint,
        ),
    )


def read_site(
    *,
    file_path: str = "app/upload.py",
    line: int = 5,
    identity_hint: str | None = None,
    source: str = 'open(path, "rb")',
) -> FamilySite:
    """A `FamilySite` holding one read site."""
    return FamilySite(
        family="fs",
        site=ReadSite(
            call_site=_call(source), file_path=file_path, line=line,
            identity_hint=identity_hint, source_type="user_upload",
        ),
    )


def upload(file_path: str = "app/upload.py") -> UploadSite:
    """One upload site, which is what puts a file in a writer's scope."""
    return UploadSite(
        entry_point=Node(
            id="entry_point:upload", kind="entry_point", name="POST /upload",
            host_id="localhost",
            attributes={"source_file": file_path, "handler": "handle"},
        ),
        file_path=file_path,
        line=1,
        framework="fastapi",
    )


@dataclass
class Case:
    """A runner input set, and a `run()` that supplies a policy."""

    ctx: object
    delta: GraphDelta
    config_path: Path
    cache_path: Path
    upload_sites: list[UploadSite] = field(default_factory=list)

    def stores(self) -> list[Node]:
        return [n for n in self.delta.nodes if n.kind == "store"]

    def edges(self, kind: str) -> list:
        return [e for e in self.delta.edges if e.kind == kind]

    def run(
        self,
        sites,
        *,
        mode: str = "auto",
        llm_call=None,
        upload_sites=None,
        imports_by_file=None,
        config_path=None,
        cache_path=None,
    ):
        """Run the fallback with a policy built from `mode`.

        Returns `(stores, edges)`. The delta is not touched: the caller
        merges, the way the stage does.
        """
        policy = default_scan_config()
        policy.llm_fallback.mode = mode
        return run(
            sites=sites,
            delta=self.delta,
            upload_sites=(
                self.upload_sites if upload_sites is None else upload_sites
            ),
            imports_by_file=(
                {} if imports_by_file is None else imports_by_file
            ),
            ctx=self.ctx,
            scan=policy,
            config_path=(
                self.config_path if config_path is None else config_path
            ),
            cache_path=self.cache_path if cache_path is None else cache_path,
            llm_call=llm_call,
        )


@pytest.fixture
def memory_cache():
    """A fallback cache in memory, closed after the test."""
    cache = FallbackCache(":memory:")
    yield cache
    cache.close()


@pytest.fixture
def case(tmp_path: Path):
    """Build a `Case`: files on disk, a delta, upload sites, a config."""

    def build(
        files: dict[str, str] | None = None,
        nodes: list[Node] | None = None,
        upload_sites: list[UploadSite] | None = None,
        config: str | None = CONFIG_YAML,
        cache: Path | None = None,
    ) -> Case:
        root = tmp_path / "target"
        root.mkdir(parents=True, exist_ok=True)
        manifest = FileManifest(target=Target(root=root, kind="folder"))
        for index, (path, source) in enumerate((files or {}).items()):
            abs_path = root / path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(source, encoding="utf-8")
            manifest.files.append(FileEntry(
                path=path,
                abs_path=abs_path,
                language="python",
                content_hash=f"h{index}",
                size=len(source),
            ))

        config_path = tmp_path / "config.yaml"
        if config is not None:
            config_path.write_text(config, encoding="utf-8")

        ctx = make_context(
            manifest, TSAOutput(), entry_points=EntryPointOutput(),
        )
        return Case(
            ctx=ctx,
            delta=GraphDelta(nodes=list(nodes or [])),
            config_path=config_path,
            cache_path=cache or (
                tmp_path / "cache" / "llm_classifier.sqlite3"
            ),
            upload_sites=list(upload_sites or []),
        )

    return build
