"""The cache's key, and its file (ADR-0023 §4, C1).

The key is the whole point: `(content_hash, prompt_version, model_id)` is
the layer's reproducibility guarantee, and each part gets a test that
changing it alone invalidates the answer. The file is the other half of
C1: **D.2's**, shared, with the `prompt_version` as the discriminator.
"""

from __future__ import annotations

import sqlite3

from aicorellator.parsing.llm_classifier import (
    PROMPT_VERSION as CLASSIFIER_VERSION,
)
from aicorellator.parsing.llm_classifier.cache import (
    CACHE_FILENAME,
    ClassifierCache,
)
from aicorellator.store.llm_fallback import PROMPT_VERSION
from aicorellator.store.llm_fallback.cache import (
    CachedIdentity,
    FallbackCache,
    content_hash,
    identity_hash,
)


KEY = ("abc123", PROMPT_VERSION, "test/model")


def test_the_file_is_d2s(tmp_path) -> None:
    """C1 — one file, one cleanup: `--clean` deletes `cache/`."""
    path = tmp_path / "cache" / CACHE_FILENAME
    with FallbackCache(path) as cache:
        cache.put(*KEY, identity_hint="docs")
    assert path.exists()
    assert path.name == "llm_classifier.sqlite3"


def test_the_two_caches_share_a_file_without_colliding(tmp_path) -> None:
    """C1 — same file, same content hash, different `prompt_version`."""
    path = tmp_path / "cache" / CACHE_FILENAME
    with ClassifierCache(path) as classifier:
        classifier.put(
            KEY[0], CLASSIFIER_VERSION, KEY[2],
            kind="tool", capabilities=["reads_filesystem"],
        )
    with FallbackCache(path) as fallback:
        assert fallback.get(KEY[0], CLASSIFIER_VERSION, KEY[2]) is None
        fallback.put(*KEY, identity_hint="docs", confidence=0.8)

    with ClassifierCache(path) as classifier:
        assert classifier.get(
            KEY[0], CLASSIFIER_VERSION, KEY[2],
        ).kind == "tool"
        assert classifier.get(*KEY) is None
    with FallbackCache(path) as fallback:
        assert fallback.get(*KEY) == CachedIdentity("docs", 0.8, "")


def test_miss_then_hit_round_trips(memory_cache: FallbackCache) -> None:
    assert memory_cache.get(*KEY) is None

    memory_cache.put(
        *KEY, identity_hint="docs", confidence=0.9, reasoning="why",
    )
    hit = memory_cache.get(*KEY)

    assert hit == CachedIdentity(
        identity_hint="docs", confidence=0.9, reasoning="why",
    )
    assert memory_cache.count() == 1


def test_a_null_hint_is_a_hit(memory_cache: FallbackCache) -> None:
    """"The code names no store" is an answer, and it is worth keeping."""
    memory_cache.put(*KEY, identity_hint=None, confidence=0.0)
    assert memory_cache.get(*KEY) == CachedIdentity(None, 0.0, "")


def test_changing_the_prompt_version_is_a_miss(
    memory_cache: FallbackCache,
) -> None:
    """Editing the prompt without bumping the version is the guarded bug."""
    memory_cache.put(*KEY, identity_hint="docs")
    assert memory_cache.get(KEY[0], "store_identity:v2.0.0", KEY[2]) is None


def test_changing_the_model_is_a_miss(memory_cache: FallbackCache) -> None:
    memory_cache.put(*KEY, identity_hint="docs")
    assert memory_cache.get(KEY[0], KEY[1], "other/model") is None


def test_changing_the_content_is_a_miss(memory_cache: FallbackCache) -> None:
    memory_cache.put(*KEY, identity_hint="docs")
    assert memory_cache.get("different", KEY[1], KEY[2]) is None


def test_put_replaces_the_same_key(memory_cache: FallbackCache) -> None:
    """One row per key: a re-run overwrites, it does not accumulate."""
    memory_cache.put(*KEY, identity_hint="docs")
    memory_cache.put(*KEY, identity_hint="uploads")

    assert memory_cache.count() == 1
    assert memory_cache.get(*KEY).identity_hint == "uploads"


def test_the_parent_directory_is_created(tmp_path) -> None:
    """The runner passes a path under `cache/`; it may not exist yet."""
    path = tmp_path / "deep" / "cache" / CACHE_FILENAME
    with FallbackCache(path):
        assert path.exists()


def test_context_manager_closes(tmp_path) -> None:
    with FallbackCache(tmp_path / "x.sqlite3") as cache:
        cache.put(*KEY, identity_hint="docs")
        assert cache.count() == 1


def test_the_table_is_its_own(tmp_path) -> None:
    """D.2's row shape is a classification; a store identity is not one."""
    path = tmp_path / "x.sqlite3"
    with FallbackCache(path):
        pass
    raw = sqlite3.connect(path)
    tables = {
        row[0] for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    raw.close()
    assert "llm_store_identities" in tables
    assert "llm_classifications" not in tables


def test_identity_hash_covers_every_prompt_input() -> None:
    """Call shape, window, file path and imports are all in the identity."""
    base = identity_hash("open", "def f(): pass", "a.py", [])
    assert base != identity_hash("s3.put_object", "def f(): pass", "a.py", [])
    assert base != identity_hash("open", "def g(): pass", "a.py", [])
    assert base != identity_hash("open", "def f(): pass", "b.py", [])
    assert base != identity_hash("open", "def f(): pass", "a.py", ["os"])


def test_the_hash_function_is_d2s() -> None:
    """Reused, not restated: the separator discipline lives in one place."""
    assert identity_hash("c", "w", "f.py", ["os"]) == content_hash(
        "c\nw", "f.py", ["os"],
    )


def test_identity_hash_is_stable() -> None:
    """Same inputs, same key — across processes, so not a salted hash."""
    assert identity_hash("open", "w", "f.py", ["os"]) == identity_hash(
        "open", "w", "f.py", ["os"],
    )
