"""Registry of framework detectors.

Detectors are selected by the user (CLI flag, config file). The
registry resolves the names to detector instances and applies
them to each file.

Naming convention matches the names used in the config:

    --frameworks langchain,langgraph,fastmcp
"""

from __future__ import annotations

import logging

from .base import FrameworkDetector
from .langchain import LangChainDetector


log = logging.getLogger(__name__)


# Registry of known detectors.
# Adding a new detector = one line here.
KNOWN_DETECTORS: dict[str, type[FrameworkDetector]] = {
    "langchain": LangChainDetector,
    # "langgraph": LangGraphDetector,      # next
    # "openai_agents": OpenAIAgentsDetector,
    # "fastmcp": FastMCPDetector,
}


def resolve(names: list[str]) -> list[FrameworkDetector]:
    """Resolve detector names to instances.

    Unknown names are logged and skipped, not fatal.
    """
    detectors: list[FrameworkDetector] = []
    for name in names:
        cls = KNOWN_DETECTORS.get(name)
        if cls is None:
            log.warning("unknown framework detector: %s", name)
            continue
        detectors.append(cls())
    return detectors


def all_names() -> list[str]:
    """List all registered detector names."""
    return sorted(KNOWN_DETECTORS.keys())