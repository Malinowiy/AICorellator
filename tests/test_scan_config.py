"""Unit tests for the scan-side loader of `config/config.yaml`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from aicorellator.scan_config import (
    DEFAULT_CONFIG_FILENAME,
    DetectorsConfig,
    ScanConfig,
    ScanConfigError,
    default_config_path,
    default_scan_config,
    load_scan_config,
    write_default_config,
)


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


FULL_SCAN = """
detectors:
  enabled:
    - langchain
    - langgraph
  disabled:
    - fastmcp

llm_classifier:
  mode: "required"
  config: "config/other.yaml"

runtime:
  mode: "python"
  test_command: "pytest -q tests/"

unclassified:
  action: "error"

logs:
  keep_archives: 0
"""


def test_defaults_match_the_documented_template() -> None:
    """An empty policy is the template's policy."""
    cfg = default_scan_config()
    assert cfg.detectors.enabled == []
    assert cfg.detectors.disabled == []
    assert cfg.llm_classifier.mode == "auto"
    assert cfg.llm_classifier.config is None
    assert cfg.runtime.mode == "off"
    assert cfg.runtime.test_command is None
    assert cfg.unclassified.action == "ask"
    assert cfg.logs.keep_archives == 5


def test_empty_file_is_defaults(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(tmp_path, ""))
    assert cfg == default_scan_config()


def test_comment_only_file_is_defaults(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(tmp_path, "# nothing here\n"))
    assert cfg == default_scan_config()


def test_valid_load(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(tmp_path, FULL_SCAN))
    assert cfg.detectors.enabled == ["langchain", "langgraph"]
    assert cfg.detectors.disabled == ["fastmcp"]
    assert cfg.llm_classifier.mode == "required"
    assert cfg.llm_classifier.config == "config/other.yaml"
    assert cfg.runtime.mode == "python"
    assert cfg.runtime.test_command == "pytest -q tests/"
    assert cfg.unclassified.action == "error"
    assert cfg.logs.keep_archives == 0


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path, 'runtime:\n  mode: "python"\nfuture_flag: true\n',
    ))
    assert cfg.runtime.mode == "python"


def test_user_detectors_is_not_a_key(tmp_path: Path) -> None:
    """A detector of your own is a file + `registry.py`, not a path.

    ADR-0016, §"Classification layers": a user detector is a file in
    `framework_detectors/` registered in `registry.py`, the same
    interface as the built-ins. So the old `user_detectors` list is
    gone from the policy — and, like any unknown key, silently
    ignored rather than honoured.
    """
    cfg = load_scan_config(_write_config(
        tmp_path, 'user_detectors: ["/opt/acme.py"]\n',
    ))
    assert not hasattr(cfg.detectors, "user_detectors")
    assert cfg == default_scan_config()



def test_other_subsystem_sections_are_not_scan_policy(
    tmp_path: Path,
) -> None:
    """`evaluation` and `llm_classifier_model` belong elsewhere.

    They are not even validated here: a malformed ensemble config
    is `config_loader`'s error to raise, not this loader's.
    """
    cfg = load_scan_config(_write_config(tmp_path, """
detectors:
  disabled: [langchain]

evaluation: 5

llm_classifier_model:
  model: "whatever"
"""))
    assert cfg.detectors.disabled == ["langchain"]
    assert cfg == ScanConfig(
        detectors=DetectorsConfig(disabled=["langchain"]),
    )


def test_runtime_mode_python_warns_until_layer_2_exists(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A policy that asks for a layer which does not exist is loud."""
    with caplog.at_level(logging.WARNING):
        cfg = load_scan_config(_write_config(
            tmp_path, 'runtime:\n  mode: "python"\n',
        ))
    assert cfg.runtime.mode == "python"
    assert "runtime.mode" in caplog.text


def test_llm_classifier_required_warns_until_it_exists(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        cfg = load_scan_config(_write_config(
            tmp_path, 'llm_classifier:\n  mode: "required"\n',
        ))
    assert cfg.llm_classifier.mode == "required"
    assert "llm_classifier.mode" in caplog.text


def test_policy_that_asks_for_what_exists_is_quiet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The defaults are implemented, so nothing is warned about."""
    with caplog.at_level(logging.WARNING):
        load_scan_config(_write_config(
            tmp_path, 'detectors:\n  disabled: ["fastmcp"]\n',
        ))
    assert caplog.records == []


def test_template_round_trip(tmp_path: Path) -> None:
    """What `ensure()` writes loads back as the defaults."""
    path = tmp_path / "config" / DEFAULT_CONFIG_FILENAME
    write_default_config(path)
    assert path.exists()
    assert load_scan_config(path) == default_scan_config()


def test_template_holds_every_subsystem_section(tmp_path: Path) -> None:
    """One file, all sections — the template is the whole file."""
    path = tmp_path / "config" / DEFAULT_CONFIG_FILENAME
    write_default_config(path)
    text = path.read_text(encoding="utf-8")
    for section in (
        "detectors:",
        "llm_classifier:",
        "runtime:",
        "evaluation:",
        "llm_classifier_model:",
        "unclassified:",
        "logs:",
    ):
        assert f"\n{section}" in text, section


def test_default_config_path() -> None:
    assert default_config_path() == (
        Path("config") / DEFAULT_CONFIG_FILENAME
    )
    assert default_config_path(Path("/ws")) == (
        Path("/ws") / "config" / DEFAULT_CONFIG_FILENAME
    )


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_scan_config(tmp_path / "missing.yaml")


def test_malformed_yaml(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "detectors: [unclosed\n")
    with pytest.raises(ScanConfigError, match="invalid YAML"):
        load_scan_config(path)


def test_root_not_mapping(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "- a\n- b\n")
    with pytest.raises(ScanConfigError, match="mapping"):
        load_scan_config(path)


@pytest.mark.parametrize(
    ("where", "content"),
    [
        (
            "llm_classifier.mode",
            'llm_classifier:\n  mode: "sometimes"\n',
        ),
        ("runtime.mode", 'runtime:\n  mode: "jvm"\n'),
        (
            "unclassified.action",
            'unclassified:\n  action: "guess"\n',
        ),
    ],
)
def test_enum_rejections(tmp_path: Path, where: str, content: str) -> None:
    """A value outside the documented set is an error, not a fallback."""
    path = _write_config(tmp_path, content)
    with pytest.raises(ScanConfigError, match=where):
        load_scan_config(path)


@pytest.mark.parametrize(
    "content",
    [
        'llm_classifier:\n  mode: "off"\n',
        'llm_classifier:\n  mode: "auto"\n',
        'llm_classifier:\n  mode: "required"\n',
        'runtime:\n  mode: "off"\n',
        'runtime:\n  mode: "python"\n',
        'unclassified:\n  action: "ask"\n',
        'unclassified:\n  action: "skip"\n',
        'unclassified:\n  action: "llm"\n',
        'unclassified:\n  action: "error"\n',
    ],
)
def test_documented_enum_value_loads(tmp_path: Path, content: str) -> None:
    load_scan_config(_write_config(tmp_path, content))  # must not raise


def test_null_enum_is_rejected(tmp_path: Path) -> None:
    """`mode:` with no value is a mistake, not `auto`."""
    path = _write_config(tmp_path, "runtime:\n  mode:\n")
    with pytest.raises(ScanConfigError, match="runtime.mode"):
        load_scan_config(path)


@pytest.mark.parametrize(
    ("where", "content"),
    [
        ("detectors.enabled", 'detectors:\n  enabled: langchain\n'),
        ("detectors.disabled", "detectors:\n  disabled: 3\n"),
        (
            "detectors.enabled",
            'detectors:\n  enabled: ["langchain", ""]\n',
        ),
        (
            "detectors.enabled",
            'detectors:\n  enabled: ["langchain", 7]\n',
        ),
    ],
)
def test_detector_names_must_be_a_list(
    tmp_path: Path, where: str, content: str
) -> None:
    path = _write_config(tmp_path, content)
    with pytest.raises(ScanConfigError, match=where):
        load_scan_config(path)


def test_name_list_is_stripped(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path, 'detectors:\n  enabled: [" langchain "]\n',
    ))
    assert cfg.detectors.enabled == ["langchain"]


def test_llm_classifier_config_must_be_a_string(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "llm_classifier:\n  config: 42\n")
    with pytest.raises(ScanConfigError, match="llm_classifier.config"):
        load_scan_config(path)


def test_test_command_is_optional(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path, 'runtime:\n  test_command: "pytest -q"\n',
    ))
    assert cfg.runtime.test_command == "pytest -q"

    cfg = load_scan_config(_write_config(
        tmp_path, 'runtime:\n  test_command: ""\n',
    ))
    assert cfg.runtime.test_command is None


def test_test_command_must_be_a_string(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "runtime:\n  test_command: 5\n")
    with pytest.raises(ScanConfigError, match="runtime.test_command"):
        load_scan_config(path)


@pytest.mark.parametrize("value", ["-1", "five", "true"])
def test_keep_archives_must_be_a_non_negative_int(
    tmp_path: Path, value: str
) -> None:
    path = _write_config(tmp_path, f"logs:\n  keep_archives: {value}\n")
    with pytest.raises(ScanConfigError, match="logs.keep_archives"):
        load_scan_config(path)


@pytest.mark.parametrize(
    ("where", "content"),
    [
        ("detectors", "detectors: langchain\n"),
        ("llm_classifier", "llm_classifier: auto\n"),
        ("runtime", "runtime: python\n"),
        ("unclassified", "unclassified: ask\n"),
        ("logs", "logs: 5\n"),
    ],
)
def test_section_not_mapping(tmp_path: Path, where: str, content: str) -> None:
    path = _write_config(tmp_path, content)
    with pytest.raises(ScanConfigError, match=f"'{where}'"):
        load_scan_config(path)


# ============================================================
# Detector resolution
# ============================================================

KNOWN = ["fastmcp", "langchain", "langgraph", "mcp_python"]


def test_resolve_detectors_empty_enabled_means_all() -> None:
    cfg = default_scan_config()
    assert cfg.resolve_detectors(KNOWN) == KNOWN


def test_resolve_detectors_subtracts_disabled(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path,
        'detectors:\n  disabled: ["langchain", "mcp_python"]\n',
    ))
    assert cfg.resolve_detectors(KNOWN) == ["fastmcp", "langgraph"]


def test_resolve_detectors_explicit_enabled(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path, 'detectors:\n  enabled: ["langgraph"]\n',
    ))
    assert cfg.resolve_detectors(KNOWN) == ["langgraph"]


def test_resolve_detectors_disabled_wins(tmp_path: Path) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path,
        "detectors:\n  enabled: [langgraph]\n  disabled: [langgraph]\n",
    ))
    assert cfg.resolve_detectors(KNOWN) == []


def test_resolve_detectors_keeps_the_order_of_enabled(
    tmp_path: Path,
) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path, "detectors:\n  enabled: [mcp_python, langchain]\n",
    ))
    assert cfg.resolve_detectors(KNOWN) == ["mcp_python", "langchain"]


def test_resolve_detectors_reports_unknown_names(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo in the policy is dropped loudly, not silently."""
    cfg = load_scan_config(_write_config(
        tmp_path, 'detectors:\n  enabled: ["langchan"]\n',
    ))
    with caplog.at_level(logging.WARNING):
        names = cfg.resolve_detectors(KNOWN)
    assert names == []
    assert "langchan" in caplog.text


def test_resolve_detectors_unknown_disabled_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = load_scan_config(_write_config(
        tmp_path, 'detectors:\n  disabled: ["nope"]\n',
    ))
    with caplog.at_level(logging.WARNING):
        names = cfg.resolve_detectors(KNOWN)
    assert names == KNOWN
    assert "nope" in caplog.text


def test_resolve_detectors_no_known_names() -> None:
    """An empty registry leaves nothing to run, without erroring."""
    assert default_scan_config().resolve_detectors([]) == []