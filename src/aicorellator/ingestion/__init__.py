"""Ingestion — target normalization and file discovery."""

from .target import Target, ingest
from .discovery import FileEntry, FileManifest, discover

__all__ = ["Target", "ingest", "FileEntry", "FileManifest", "discover"]