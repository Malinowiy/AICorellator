import importlib
import json
import pkgutil
from datetime import datetime, timezone
from pathlib import Path

from aicorellator.models import ScanResult, to_dict
from aicorellator.modules.base import ScannerModule


def discover_modules(package: str = "aicorellator.modules") -> list[type[ScannerModule]]:
    pkg = importlib.import_module(package)
    found: list[type[ScannerModule]] = []
    for _, mod_name, _ in pkgutil.iter_modules(pkg.__path__):
        if mod_name in ("base", "__init__"):
            continue
        module = importlib.import_module(f"{package}.{mod_name}")
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, ScannerModule)
                and attr is not ScannerModule
                and attr.__module__.startswith(package)
            ):
                found.append(attr)
    return found


def run_all(strict: bool = False) -> list[ScanResult]:
    results: list[ScanResult] = []
    for cls in discover_modules():
        mod = cls()
        print(f"[runner] {mod.name}: ", end="", flush=True)

        if not mod.is_available():
            print("SKIP (not installed)")
            if mod.required and strict:
                raise RuntimeError(f"required module {mod.name} not available")
            continue

        result = mod.run()

        if result.error:
            print(f"FAIL ({result.error})")
            if mod.required and strict:
                raise RuntimeError(f"required module {mod.name} failed: {result.error}")
        else:
            print(f"OK ({len(result.nodes)} nodes, {len(result.edges)} edges)")

        results.append(result)
    return results


def save_results(results: list[ScanResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(exist_ok=True)

    for r in results:
        (out_dir / "raw" / f"{r.tool}.json").write_text(
            json.dumps(r.raw, indent=2, ensure_ascii=False)
        )

        with (out_dir / "nodes.jsonl").open("a") as f:
            for n in r.nodes:
                f.write(json.dumps(to_dict(n), ensure_ascii=False) + "\n")

        with (out_dir / "edges.jsonl").open("a") as f:
            for e in r.edges:
                f.write(json.dumps(to_dict(e), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    results = run_all(strict=False)
    scan_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path.home() / ".aicorellator" / "scans" / scan_id
    save_results(results, out_dir)
    total_nodes = sum(len(r.nodes) for r in results)
    total_edges = sum(len(r.edges) for r in results)
    print(f"\n[runner] saved to {out_dir}")
    print(f"[runner] total: {total_nodes} nodes, {total_edges} edges")