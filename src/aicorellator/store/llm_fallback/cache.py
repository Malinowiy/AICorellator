"""Fallback cache — the fallback's answer, keyed for reuse (ADR-0023 §4).

The cache is not an optimisation. It is what makes the fallback
*reproducible*: an LLM is non-deterministic, so the same corpus rescanned
would otherwise produce a different graph. The second scan of an unchanged
file serves the answers the first scan stored.

**The same file as D.2's** (`cache/llm_classifier.sqlite3`), with its own
table. `--clean` deletes `cache/`, and a second cache directory is a
second thing to clean; the `prompt_version` is the discriminator, so a
classifier answer and a fallback answer for the same symbol never collide.
One file, one cleanup.

One row per key, in `llm_store_identities`:

    (content_hash, prompt_version, model_id) → (identity_hint, confidence)

The three parts of the key are the three ways an answer stops being valid:

  - `content_hash` — the site's call shape, its code window, its file path
    or its file's imports changed. Those are the user prompt's inputs
    (`prompt.build_user_prompt`), so any of them changing may change the
    answer. The hash *function* is D.2's (`llm_classifier.cache.
    content_hash`), reused rather than copied: it is the same three-part
    payload (`source`, `file_path`, `imports`) with the fallback's own
    `source` — the call shape and the window. The system prompt and the
    few-shot examples are not hashed: they are source text, and they are
    versioned by `prompt_version`.
  - `prompt_version` — `prompt.PROMPT_VERSION`
    (`"store_identity:v1.0.0"`), which is what keeps the two prompts'
    entries apart in the shared file.
  - `model_id` — a different model answers differently (`config.model`).

Deliberately *not* in the key: `temperature`, `max_tokens`, `timeout`,
`base_url`, for D.2's reasons — the last two do not change the answer at
all, and including them would invalidate a whole corpus cache because a
user raised a timeout.

A `null` answer is cached: "the code names no store" is an answer, and
re-asking for it costs a call to say the same thing. `identity_hint` is
therefore a nullable column, and a **miss** is the absence of a row, not
a null value in one.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ...graph.models import utcnow
from ...parsing.llm_classifier.cache import (
    CACHE_FILENAME,
    content_hash,
)


log = logging.getLogger(__name__)


#: The shared file's table. Its own, because D.2's row shape is a
#: classification (`kind`, `capabilities`) and a store identity is not one.
_TABLE = "llm_store_identities"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    content_hash   TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_id       TEXT NOT NULL,
    identity_hint  TEXT,               -- NULL is a valid answer
    confidence     REAL NOT NULL DEFAULT 0.0,
    reasoning      TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    PRIMARY KEY (content_hash, prompt_version, model_id)
);
"""


@dataclass(frozen=True)
class CachedIdentity:
    """One stored answer, ready to be turned into a store."""

    identity_hint: str | None
    confidence: float = 0.0
    reasoning: str = ""


def identity_hash(
    call_shape: str,
    window: str,
    file_path: str,
    imports: Sequence[str],
) -> str:
    """Hash the fallback prompt's inputs, with D.2's function.

    `content_hash(source, file_path, imports)` is exactly the shape: the
    fallback's `source` is the site's call shape and the code window it
    was asked about, and the other two are the file's path and its
    imports. Reusing the function keeps the two subsystems' keys
    comparable and their separator discipline (a byte a path or an import
    cannot contain) in one place.
    """
    return content_hash(f"{call_shape}\n{window}", file_path, imports)


class FallbackCache:
    """The `llm_store_identities` table, as an object.

    Pass `":memory:"` for a cache that is not on disk (the D.2
    convention). The connection is not thread-safe and does not need to
    be: the fallback runs in one process, in one stage.
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
    ) -> CachedIdentity | None:
        """The stored answer for this key, or None.

        A row whose `identity_hint` is NULL is a *hit*: it says the model
        answered "the code names no store", which is a fact about the
        code and worth not re-asking.
        """
        row = self._conn.execute(
            f"SELECT identity_hint, confidence, reasoning FROM {_TABLE} "
            "WHERE content_hash = ? AND prompt_version = ? AND model_id = ?",
            (content_hash, prompt_version, model_id),
        ).fetchone()
        if row is None:
            return None

        identity_hint, confidence, reasoning = row
        return CachedIdentity(
            identity_hint=identity_hint,
            confidence=float(confidence or 0.0),
            reasoning=reasoning or "",
        )

    def put(
        self,
        content_hash: str,
        prompt_version: str,
        model_id: str,
        *,
        identity_hint: str | None,
        confidence: float = 0.0,
        reasoning: str = "",
    ) -> None:
        """Store one answer, replacing any row with the same key."""
        self._conn.execute(
            f"INSERT OR REPLACE INTO {_TABLE} ("
            "  content_hash, prompt_version, model_id, identity_hint,"
            "  confidence, reasoning, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                content_hash,
                prompt_version,
                model_id,
                identity_hint,
                confidence,
                reasoning,
                utcnow(),
            ),
        )
        self._conn.commit()

    def count(self) -> int:
        """Stored answers — the runner reports it, tests assert on it."""
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM {_TABLE}"
        ).fetchone()
        return int(row[0]) if row else 0

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FallbackCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


__all__ = [
    "CACHE_FILENAME",
    "CachedIdentity",
    "FallbackCache",
    "content_hash",
    "identity_hash",
]
