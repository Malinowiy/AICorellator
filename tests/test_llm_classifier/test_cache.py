"""The cache's key, and what it does with a bad row (D.2).

The key is the whole point: `(content_hash, prompt_version, model_id)` is
the layer's reproducibility guarantee, so each of the three parts gets a
test that changing it alone invalidates the answer.
"""

from __future__ import annotations

import sqlite3

from aicorellator.parsing.llm_classifier.cache import (
    CACHE_FILENAME,
    CachedClassification,
    ClassifierCache,
    content_hash,
)


KEY = ("abc123", "v1.0.0", "test/model")


def test_miss_then_hit_round_trips(memory_cache: ClassifierCache) -> None:
    assert memory_cache.get(*KEY) is None

    memory_cache.put(
        *KEY, kind="tool", capabilities=["reads_filesystem"], reasoning="why"
    )
    hit = memory_cache.get(*KEY)

    assert hit == CachedClassification(
        kind="tool",
        capabilities=("reads_filesystem",),
        reasoning="why",
    )
    assert memory_cache.count() == 1


def test_changing_the_prompt_version_is_a_miss(
    memory_cache: ClassifierCache,
) -> None:
    """Editing the prompt without bumping the version is the guarded failure."""
    memory_cache.put(*KEY, kind="tool", capabilities=[])
    assert memory_cache.get(KEY[0], "v2.0.0", KEY[2]) is None


def test_changing_the_model_is_a_miss(memory_cache: ClassifierCache) -> None:
    memory_cache.put(*KEY, kind="tool", capabilities=[])
    assert memory_cache.get(KEY[0], KEY[1], "other/model") is None


def test_changing_the_content_is_a_miss(memory_cache: ClassifierCache) -> None:
    memory_cache.put(*KEY, kind="tool", capabilities=[])
    assert memory_cache.get("different", KEY[1], KEY[2]) is None


def test_put_replaces_the_same_key(memory_cache: ClassifierCache) -> None:
    """One row per key: a re-run overwrites, it does not accumulate."""
    memory_cache.put(*KEY, kind="tool", capabilities=["writes_db"])
    memory_cache.put(*KEY, kind="sink", capabilities=["executes_shell"])

    assert memory_cache.count() == 1
    assert memory_cache.get(*KEY).kind == "sink"


def test_capabilities_are_stored_sorted_and_deduplicated(
    memory_cache: ClassifierCache,
) -> None:
    memory_cache.put(
        *KEY,
        kind="agent",
        capabilities=["calls_llm", "reads_env", "calls_llm"],
    )
    assert memory_cache.get(*KEY).capabilities == ("calls_llm", "reads_env")


def test_missing_capabilities_stay_empty(memory_cache: ClassifierCache) -> None:
    """An answer with no capabilities is an answer, not a hole."""
    memory_cache.put(*KEY, kind="tool", capabilities=[])
    assert memory_cache.get(*KEY).capabilities == ()


def test_a_corrupt_row_is_a_miss(tmp_path) -> None:
    """A row this module did not write must not take down a scan."""
    path = tmp_path / "cache" / CACHE_FILENAME
    cache = ClassifierCache(path)
    cache.put(*KEY, kind="tool", capabilities=[])

    raw = sqlite3.connect(path)
    raw.execute(
        "UPDATE llm_classifications SET capabilities = ? WHERE content_hash = ?",
        ("not json", KEY[0]),
    )
    raw.commit()
    raw.close()

    assert cache.get(*KEY) is None
    cache.close()


def test_the_parent_directory_is_created(tmp_path) -> None:
    """The runner passes a path under `cache/`; it may not exist yet."""
    path = tmp_path / "deep" / "cache" / CACHE_FILENAME
    cache = ClassifierCache(path)
    assert path.exists()
    cache.close()


def test_context_manager_closes(tmp_path) -> None:
    with ClassifierCache(tmp_path / "x.sqlite3") as cache:
        cache.put(*KEY, kind="tool", capabilities=[])
        assert cache.count() == 1


def test_content_hash_covers_every_prompt_input() -> None:
    """Source, file path and imports are all part of the identity."""
    base = content_hash("def f(): pass", "a.py", [])
    assert base != content_hash("def f(): pass", "b.py", [])
    assert base != content_hash("def g(): pass", "a.py", [])
    assert base != content_hash("def f(): pass", "a.py", ["import os"])


def test_content_hash_fields_cannot_bleed_into_each_other() -> None:
    """A separator that a path or an import cannot contain.

    Without it, `("a", "b")` and `("a\\nb", "")` would hash the same string
    and one symbol's cached answer could be served for another.
    """
    assert content_hash("x", "a", ["b"]) != content_hash("x", "a\nb", [])
    assert content_hash("a", "b", []) != content_hash("b", "a", [])


def test_content_hash_is_stable() -> None:
    """Same inputs, same key — across processes, so not a salted hash."""
    assert content_hash("src", "f.py", ["os"]) == content_hash(
        "src", "f.py", ["os"]
    )
