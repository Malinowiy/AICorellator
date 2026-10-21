"""LlmEndpointStage — `llm_endpoint` nodes from LLM configs.

Run order: 6 of 14 (see `normalizer_stages.STAGES`).

C4: "**LLM endpoint nodes** — from the LLM extractor."

The extractor reads config files (`.env`, `.py`, `.yaml`, ...), so
several files may describe the same endpoint; `deduplicated()` collapses
them before a node is built. The id is a hash of provider + model +
base_url, so two configs for the same model produce one node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...graph.models import Node
from ..normalizer_context import NormalizeContext
from ..normalizer_helpers import (
    HOST_ID,
    llm_endpoint_id,
    llm_endpoint_name,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..ast_normalizer import GraphDelta


class LlmEndpointStage:
    """One node per distinct configured LLM endpoint."""

    name = "llm_endpoints"

    def apply(
        self, delta: "GraphDelta", ctx: NormalizeContext
    ) -> "GraphDelta":
        for llm_ep in ctx.llm_output.deduplicated():
            delta.nodes.append(Node(
                id=llm_endpoint_id(llm_ep),
                kind="llm_endpoint",
                name=llm_endpoint_name(llm_ep),
                host_id=HOST_ID,
                attributes={
                    "source_file": llm_ep.source_file,
                    "provider": llm_ep.provider,
                    "model": llm_ep.model or "",
                    "base_url": llm_ep.base_url or "",
                },
            ))
        return delta
