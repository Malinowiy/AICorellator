"""Streamlit uploads — a file whose `st.file_uploader` is the entry point.

ADR-0021 §8 (revision 2) makes an entry point that *accepts* an upload a
**context-based writer**: the stores it writes into are its stores at the
context-based writer's confidence. `UploadDetector` is the interface that
answers "does this entry point accept an upload" — one module per
framework, ADR-0011's shape — and `fastapi` was the first implementation,
function-level: it reads a handler's signature. Streamlit is the second
and the first **module-level** one, because a Streamlit app's entry point
is the module the call sits in:

    uploaded = st.file_uploader("Upload")    → one site

**The entry point is the module.** `entry_points.EntryPointStage` builds
one node per distinct `st.<call>` at module level — `method="STREAMLIT"`,
`entry_kind="streamlit"`, `path` = the call's name, `line_start` = the
call's line, and **no** `handler`, because there is no function to name.
The writer's module-level branch (`writer._module_level`) is what reads
such a site: the module's top-level functions and its own body are the
writes the entry point reaches, and the site is the module's rather than
a function's.

**The detector does not walk the tree.** The node carries `source_file`,
`line_start`, `path` and `entry_kind`, which is everything a site is made
of, so `tree` — part of the interface, and passed by the context — is not
read. A Streamlit upload is a fact about the entry point the scan already
found, and re-parsing for it would be a second answer to a question the
`entry_points` stage has already answered.

`path` is the filter, and it is the whole filter. `STREAMLIT_ENTRY_CALLS`
holds four calls — `chat_input`, `form_submit_button`, `button`,
`file_uploader` — and exactly one of them accepts a file: `st.chat_input`
is a text entry, and `st.button` and `st.form_submit_button` are clicks.
A file whose only Streamlit calls are those declares no upload, and this
detector returns `[]` for it, so its entry points stay what they were —
entry points that are not writers. A future Streamlit upload call
(`st.camera_input`, `st.audio_input`) is a member added here, in its own
step with its own test, not a shape guessed today.

**One site per file.** A file with two `st.file_uploader` calls has *one*
entry point — `_detect_streamlit_entries` dedupes by `child.func.attr`,
and two calls to one call name are one entry point — so it is one site. A
file with a `button` and a `file_uploader` has one entry point per
distinct call name, and it is still one site: the `file_uploader` one,
because only that node passes the filter. The count of sites is the
count of uploads the file declares, and an upload is the module's.

Sites come back in `entry_points` order — delta order, so two runs over
one file are identical — and the context deduplicates by
`(entry_point.id, framework)`, exactly as it does for `fastapi`.
"""

from __future__ import annotations

import ast

from .....graph.models import Node
from ..base import UploadDetector, UploadSite

#: The registry key, and the `framework` every site is stamped with. The
#: class declares the same name, so the registry's name-vs-class warning
#: (`registry.register_upload_detector`) stays quiet.
NAME = "streamlit"

#: The `entry_kind` `entry_points.EntryPointStage` stamps on a Streamlit
#: node, and the writer's own marker for a module-level entry
#: (`writer.MODULE_LEVEL_ENTRY_KINDS`). Read here as the kind of the entry
#: point being claimed — a node the stage built for an HTTP route carries
#: `handler` instead, and one it built for a CLI or an MCP entry carries
#: `"cli"` or `"mcp"`.
ENTRY_KIND = "streamlit"

#: The one Streamlit call that accepts an upload, as the stage records it
#: in `path`. `STREAMLIT_ENTRY_CALLS` holds three more and none of them is
#: a file: this is the filter, and the module docstring is why it is the
#: only one.
UPLOAD_PATH = "file_uploader"


class StreamlitUploadDetector(UploadDetector):
    """A Streamlit file whose `st.file_uploader` is its entry point."""

    name = NAME

    def detect(
        self,
        tree: ast.Module,
        file_path: str,
        entry_points: list[Node],
    ) -> list[UploadSite]:
        """One site per Streamlit entry point of `file_path` that uploads.

        `entry_points` are the file's entry-point nodes, so a site names
        the node the writer's module-level branch reads and the edge's
        source is the same object. The filter is the two attributes the
        rule names (`_is_upload_entry`); `tree` and `file_path` are part
        of the interface and are not read, because the node carries the
        file, the line and the call name already.

        One site per matching node, and the stage builds one node per
        distinct Streamlit call name — so a file with two
        `st.file_uploader` calls is one site, and a file with a `button`
        and a `file_uploader` is one site too (the `button` node does not
        match). Both are the module docstring's "one site per file", and
        `tests/test_store_upload_streamlit/` pins them.
        """
        return [
            UploadSite(
                entry_point=node,
                file_path=node.attributes["source_file"],
                line=node.attributes["line_start"],
                framework=self.name,
            )
            for node in entry_points
            if _is_upload_entry(node)
        ]


def _is_upload_entry(node: Node) -> bool:
    """True if an entry-point node is a Streamlit file uploader.

    The two attributes the rule names, and nothing else: `entry_kind` is
    the stage's own word for "this entry point is a Streamlit one", and
    `path` is the call it found. A node built for an HTTP or WebSocket
    route carries a `handler` and no `entry_kind`, a CLI node carries
    `entry_kind="cli"`, an MCP node carries `entry_kind="mcp"` and no
    `path` at all, and a hand-built node carrying a `path` without the
    kind is not a Streamlit entry either.

    The node the stage builds carries `source_file` and `line_start` as
    well, which is what makes it describable as a site; both are read by
    `detect` and neither is part of this test, so a node missing them is
    a node the context's `_detect` guard logs rather than a node this
    function silently half-claims.
    """
    return (
        node.attributes.get("entry_kind") == ENTRY_KIND
        and node.attributes.get("path") == UPLOAD_PATH
    )


__all__ = [
    "ENTRY_KIND",
    "NAME",
    "UPLOAD_PATH",
    "StreamlitUploadDetector",
]
