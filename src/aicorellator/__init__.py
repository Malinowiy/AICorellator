"""AICorellator — AI Infrastructure Attack Surface Correlator."""

__version__ = "0.1.0a1"

try:
    from .graph import GraphStore
except ImportError as e:
    raise ImportError(f"failed to import GraphStore: {e}") from e

__all__ = ["GraphStore", "__version__"]