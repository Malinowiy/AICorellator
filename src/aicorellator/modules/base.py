from abc import ABC, abstractmethod

from aicorellator.models import ScanResult


class ScannerModule(ABC):
    """
    Base interface for a scanner module.

    Each module:
      1. Runs its external tool (subprocess, API, etc.)
      2. Parses its output
      3. Normalizes it into a ScanResult (Node/Edge/Provenance)
    """

    name: str = "unnamed"
    command: str | None = None
    required: bool = False

    @abstractmethod
    def run(self) -> ScanResult:
        """Runs the tool and returns the normalized result."""
        ...

    def is_available(self) -> bool:
        """Checks that the tool is installed and available."""
        return True