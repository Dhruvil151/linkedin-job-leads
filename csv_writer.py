"""
Thread-safe, append-only CSV writer that streams rows in real time.

If the script is interrupted, all rows written up to that point are
safely persisted on disk because we flush after every write.
"""

import csv
import os
import threading
from dataclasses import asdict, dataclass, fields
from typing import Optional


@dataclass
class LeadRow:
    """One row of output data."""
    author_name: str
    post_text_snippet: str
    email: str
    timestamp: str          # ISO-8601 capture timestamp
    post_url: str


_FIELDNAMES = [f.name for f in fields(LeadRow)]


class CSVWriter:
    """Append-only CSV writer with automatic header creation."""

    def __init__(self, filepath: str) -> None:
        self._filepath = filepath
        self._lock = threading.Lock()
        self._file = None
        self._writer: Optional[csv.DictWriter] = None
        self._open()

    # ── lifecycle ────────────────────────────────────────────────────────
    def _open(self) -> None:
        file_exists = os.path.isfile(self._filepath)
        self._file = open(self._filepath, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=_FIELDNAMES)
        if not file_exists or os.path.getsize(self._filepath) == 0:
            self._writer.writeheader()
            self._file.flush()

    def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.close()

    # ── public API ───────────────────────────────────────────────────────
    def write_lead(self, lead: LeadRow) -> None:
        """Write a single lead row and flush immediately."""
        with self._lock:
            if self._writer is None:
                raise RuntimeError("CSVWriter is not open.")
            self._writer.writerow(asdict(lead))
            self._file.flush()

    # ── context manager ──────────────────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
