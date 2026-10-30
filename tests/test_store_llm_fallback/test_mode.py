"""The three modes, and the no-op `off` is (ADR-0023 §6, N1).

`off` → no call, no store, no edge, and the model section is never read.
`auto` → the call happens; a failure is a warning (tested in
`test_runner.py`, where the failure paths live). `required` → a failure is
a scan error.

The stage-level no-op — the delta the pipeline sees — is pinned in
`tests/test_normalizer_stages/test_store_resolver.py`, where the stage's
fixtures are.
"""

from __future__ import annotations

import pytest

from aicorellator.store.llm_fallback import FallbackConfigError

from .conftest import FakeLLM, answer, upload, write_site
from .test_runner import HANDLER, SOURCE, WRITE_LINE, _case


def test_off_makes_no_call_and_returns_nothing(case) -> None:
    """N1 — the default. No request, no store, no edge."""
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], mode="off", llm_call=fake,
    )

    assert fake.calls == []
    assert stores == []
    assert edges == []


def test_off_never_reads_the_model_section(case) -> None:
    """A scan that does not call the layer must not need its config.

    No config file is written at all: `off` has to return before anything
    tries to load `llm_fallback_model` (D.2's rule for `mode: off`).
    """
    fake = FakeLLM()
    built = _case(case, config=None)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], mode="off", llm_call=fake,
    )

    assert fake.calls == []
    assert stores == []
    assert edges == []


def test_off_does_not_open_the_cache(case) -> None:
    """No call, no rows: the cache is not written for a scan that is off."""
    built = _case(case)

    built.run([write_site(line=WRITE_LINE)], mode="off", llm_call=FakeLLM())

    assert not built.cache_path.exists()


def test_off_is_the_policys_default(case) -> None:
    """The layer is opt-in: `default_scan_config()` says `off`."""
    from aicorellator.scan_config import default_scan_config

    assert default_scan_config().llm_fallback.mode == "off"


def test_auto_is_what_calls_the_model(case) -> None:
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], mode="auto", llm_call=fake,
    )

    assert len(fake.calls) == 1
    assert len(stores) == 1
    assert len(edges) == 1


def test_required_is_satisfied_when_every_site_is_named(case) -> None:
    """`required` is a statement about the corpus, not a failure mode."""
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case)

    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], mode="required", llm_call=fake,
    )

    assert len(stores) == 1
    assert len(edges) == 1


@pytest.mark.parametrize("mode", ["auto", "required"])
def test_a_missing_model_section_is_an_error_when_the_layer_runs(
    case, mode: str,
) -> None:
    """Asking for a layer you have not configured is worse than not asking."""
    fake = FakeLLM(answer("/uploads/"))
    built = _case(case, config="detectors:\n  enabled: []\n")

    with pytest.raises(FallbackConfigError, match="llm_fallback_model"):
        built.run([write_site(line=WRITE_LINE)], mode=mode, llm_call=fake)

    assert fake.calls == []


def test_a_missing_config_file_is_an_error_when_the_layer_runs(case) -> None:
    """No file is the same mistake as no section, one step earlier."""
    fake = FakeLLM()
    built = _case(case, config=None)

    with pytest.raises(FallbackConfigError, match="cannot read"):
        built.run([write_site(line=WRITE_LINE)], mode="auto", llm_call=fake)

    assert fake.calls == []


def test_the_error_comes_before_any_call_even_with_no_gap(case) -> None:
    """The section is read for the mode, not for the site that needs it."""
    fake = FakeLLM()
    built = case(
        files={HANDLER: SOURCE},
        config="detectors:\n  enabled: []\n",
    )

    with pytest.raises(FallbackConfigError, match="llm_fallback_model"):
        built.run([], mode="auto", llm_call=fake)

    assert fake.calls == []


def test_off_with_no_upload_site_is_still_a_no_op(case) -> None:
    """Two ways to be a no-op, and they compose."""
    fake = FakeLLM()
    built = case(files={HANDLER: SOURCE}, upload_sites=[])

    stores, edges = built.run(
        [write_site(line=WRITE_LINE)], mode="off", llm_call=fake,
    )

    assert fake.calls == []
    assert stores == []


def test_a_scan_with_no_unresolved_site_is_a_no_op(case) -> None:
    """`auto` with nothing to ask is a no-op, not an error."""
    fake = FakeLLM()
    built = case(
        files={HANDLER: SOURCE}, upload_sites=[upload()],
    )

    stores, edges = built.run(
        [write_site(line=WRITE_LINE, identity_hint="/uploads/")],
        mode="auto", llm_call=fake,
    )

    assert fake.calls == []
    assert stores == []
    assert edges == []
