import json
import shutil
import subprocess

from aicorellator.models import ScanResult, Node, Edge, Provenance, now_iso
from aicorellator.modules.base import ScannerModule

class NeuralScanModule(ScannerModule):
    name = "neuralscan"
    command = "neuralscan"
    required = False

    TYPE_MAP = {
        "claude-client": "agent",
        "chatgpt-client": "agent",
        "copilot-client": "agent",
        "perplexity-client": "agent",
        "claude-connector": "tool",
        "chatgpt-connector": "tool",
        "copilot-connector": "tool",
        "perplexity-connector": "tool",
        "local-llm": "llm_endpoint",
        "process": "process",
        "ide-extension": "tool",
        "resource": "sink",
    }

    EDGE_MAP = {
        ("data-flow", "client"): "uses_client",
        ("data-flow", "connector"): "provides_tool",
        ("inferred", None): "runs_on",
        ("detected", None): "runs_on",
        ("reach", None): "has_access_to",
    }

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None

    def run(self) -> ScanResult:
        ts = now_iso()

        try:
            proc = subprocess.run(
                [self.command, "scan"],
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired as e:
            return ScanResult(tool=self.name, timestamp=ts, error=f"timeout: {e}")
        except FileNotFoundError:
            return ScanResult(tool=self.name, timestamp=ts, error="not installed")

        if proc.returncode != 0:
            return ScanResult(
                tool=self.name, timestamp=ts,
                error=f"exit {proc.returncode}: {proc.stderr[:500]}",
            )

        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            return ScanResult(tool=self.name, timestamp=ts, error=f"bad json: {e}")

        return ScanResult(
            tool=self.name, timestamp=ts,
            nodes=self._parse_nodes(raw, ts),
            edges=self._parse_edges(raw, ts),
            raw=raw,
        )

    def _parse_nodes(self, raw: dict, ts: str) -> list[Node]:
        out = []
        for n in raw.get("nodes", []):
            out.append(Node(
                id=n["id"],
                kind=self.TYPE_MAP.get(n.get("type", ""), "unknown"),
                name=n.get("name", n["id"]),
                attributes={
                    "provider": n.get("provider"),
                    "status": n.get("status"),
                    "risk": n.get("risk"),
                    "capabilities": n.get("capabilities"),
                    "classification": n.get("classification"),
                    **(n.get("metadata") or {}),
                },
                provenance=[Provenance(
                    tool=self.name, timestamp=ts,
                    evidence={"type": n.get("type"), "evidence": n.get("evidence")},
                )],
            ))
        return out

    def _parse_edges(self, raw: dict, ts: str) -> list[Edge]:
        out = []
        for e in raw.get("edges", []):
            key = (e.get("type"), e.get("label"))
            kind = self.EDGE_MAP.get(key) or self.EDGE_MAP.get(
                (e.get("type"), None), "related_to"
            )
            out.append(Edge(
                source_id=e["from"],
                target_id=e["to"],
                kind=kind,
                attributes={
                    "label": e.get("label"),
                    "risk": e.get("risk"),
                    "exfil": e.get("exfil"),
                },
                provenance=[Provenance(tool=self.name, timestamp=ts)],
            ))
        return out