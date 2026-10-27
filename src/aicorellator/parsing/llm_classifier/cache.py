"""Classification cache — the classifier's answer, keyed for reuse (D.2).

The cache is not an optimisation. It is what makes Layer 1b *reproducible*:
an LLM is non-deterministic, so the same corpus rescanned would otherwise
produce a different graph (ADR-0016, §Risks: "non-determinism without
cache. The cache is mandatory."). With it, the second scan of an unchanged
file serves the answers the first scan stored, and the graph is stable.

One row per key:

    (content_hash, prompt_version, model_id) → (kind, capabilities, reasoning)

The three parts of the key are exactly the three ways an answer stops
being valid:

  - `content_hash` — the symbol's text, its file path or its file's
    imports changed. Those are the user prompt's inputs
    (`prompt.build_user_prompt`), so any of them changing may change the
    answer. The system prompt and the few-shot examples are *not* hashed
    here: they are source text, and they are versioned by `prompt_version`.
  - `prompt_version` — `prompt.PROMPT_VERSION`. Editing the prompt without
    bumping the version serves stale answers against a prompt that no
    longer exists. That is the failure mode the constant guards against.
  - `model_id` — a different model answers differently (`config.model`).

Deliberately *not* in the key: `temperature`, `max_tokens`, `timeout`,
`base_url`. The last two do not change the answer at all, and including
them would invalidate a whole corpus cache because a user raised a timeout.
Temperature is pinned to 0.0 in `config.py` for the same reason it is not
in the key.

SQLite, one file, in the workspace's `cache/` directory: `--clean` deletes
that directory, so the cache is state, not user input (ADR-0016, §Scan
policy). No migration — the database is recreated per scan (ADR-0003), and
a cache miss costs one LLM call, not correctness.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ...graph.models import utcnow


log = logging.getLogger(__name__)


CACHE_FILENAME = "llm_classifier.sqlite3"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_classifications (
    content_hash   TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_id       TEXT NOT NULL,
    kind           TEXT NOT NULL,
    capabilities   TEXT NOT NULL,      -- JSON array of capability strings
    reasoning      TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (content_hash, prompt_version, model_id)
);
"""


@dataclass(frozen=True)
class CachedClassification:
    """One stored answer, ready to be turned into a node."""

    kind: str
    capabilities: tuple[str, ...]
    reasoning: str = ""


def content_hash(
    symbol_source: str,
    file_path: str,
    imports: Sequence[str],
) -> str:
    """Hash the user prompt's inputs: symbol text, file path, imports.

    A separator that cannot appear in a path or an import name keeps the
    fields from bleeding into each other, so `("a", "b")` and `("a\\nb",
    "")` cannot collide. The digest is not salted and not secret: it is an
    identity, not a security boundary.
    """
    payload = "\x1f".join([file_path, *imports, symbol_source])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ClassifierCache:
    """The `llm_classifications` table, as an object.

    `path` may be `":memory:"` (tests). A real path gets its parent
    directory created — the workspace's `cache/` directory exists by the
    time the runner builds this, and a path built by hand may not.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --------------------------------------------------------
    # Reads and writes
    # --------------------------------------------------------

    def get(
        self,
        content_hash: str,
        prompt_version: str,
        model_id: str,
    ) -> CachedClassification | None:
        """The stored answer for this key, or None.

        An unreadable `capabilities` column is treated as a miss: it means
        the row was written by something other than this module, and one
        corrupt row must not take down a scan.
        """
        row = self._conn.execute(
            "SELECT kind, capabilities, reasoning FROM llm_classifications "
            "WHERE content_hash = ? AND prompt_version = ? AND model_id = ?",
            (content_hash, prompt_version, model_id),
        ).fetchone()
        if row is None:
            return None

        kind, capabilities_json, reasoning = row
        try:
            capabilities = json.loads(capabilities_json)
        except json.JSONDecodeError:
            log.warning(
                "cache: unreadable capabilities for %s (treated as a miss)",
                content_hash[:12],
            )
            return None
        if not isinstance(capabilities, list):
            return None

        return CachedClassification(
            kind=kind,
            capabilities=tuple(str(c) for c in capabilities),
            reasoning=reasoning or "",
        )

    def put(
        self,
        content_hash: str,
        prompt_version: str,
        model_id: str,
        *,
        kind: str,
        capabilities: Sequence[str],
        reasoning: str = "",
    ) -> None:
        """Store one answer, replacing any row with the same key.

        `capabilities` is stored sorted and deduplicated: the set is what
        matters, and a stable order keeps the file diffable if anyone ever
        looks at it.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO llm_classifications ("
            "  content_hash, prompt_version, model_id, kind, capabilities,"
            "  reasoning, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                content_hash,
                prompt_version,
                model_id,
                kind,
                json.dumps(sorted(set(capabilities))),
                reasoning,
                utcnow(),
            ),
        )
        self._conn.commit()

    def count(self) -> int:
        """Stored answers — the runner reports it, tests assert on it."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM llm_classifications"
        ).fetchone()
        return int(row[0]) if row else 0

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ClassifierCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
