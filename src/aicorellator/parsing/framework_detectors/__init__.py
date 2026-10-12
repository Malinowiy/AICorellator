"""Framework detectors — recognize AI frameworks in Python AST."""

from .base import DetectionResult, FrameworkDetector
from .registry import KNOWN_DETECTORS, all_names, resolve

__all__ = [
    "DetectionResult",
    "FrameworkDetector",
    "KNOWN_DETECTORS",
    "all_names",
    "resolve",
]