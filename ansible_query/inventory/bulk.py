from collections.abc import Iterable
from pathlib import Path
from typing import Any


class BulkBuffer:
    """Accumulates pending file writes before atomic flush in bulk mode.

    Content is stored as raw Python objects (dicts); serialisation and
    encryption are handled by the Store at flush time.
    """

    def __init__(self) -> None:
        self._pending: dict[Path, dict[str, Any]] = {}
        self._dirs_to_remove: list[Path] = []
        self._affected_hosts: set[str] = set()

    # ── staging ───────────────────────────────────────────────────────────────

    def stage_file(
        self,
        path: Path,
        content: dict[str, Any],
        affected_hosts: Iterable[str] = (),
    ) -> None:
        """Stage a full file write. Repeated calls for the same path overwrite."""
        self._pending[path] = content
        self._affected_hosts.update(affected_hosts)

    def stage_dir_removal(self, path: Path) -> None:
        """Mark a directory for removal (e.g. hostvars/<host>/ on DROP HOST)."""
        self._dirs_to_remove.append(path)

    # ── inspection ────────────────────────────────────────────────────────────

    @property
    def pending_files(self) -> dict[Path, dict[str, Any]]:
        return dict(self._pending)

    @property
    def pending_dir_removals(self) -> list[Path]:
        return list(self._dirs_to_remove)

    @property
    def affected_hosts(self) -> frozenset[str]:
        return frozenset(self._affected_hosts)

    def get_staged(self, path: Path) -> dict[str, Any] | None:
        """Return the staged content for *path*, or None if not yet staged."""
        return self._pending.get(path)

    def is_empty(self) -> bool:
        return not self._pending and not self._dirs_to_remove

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def clear(self) -> None:
        self._pending.clear()
        self._dirs_to_remove.clear()
        self._affected_hosts.clear()

    def __repr__(self) -> str:
        return (
            f"BulkBuffer("
            f"{len(self._pending)} files, "
            f"{len(self._dirs_to_remove)} removals, "
            f"{len(self._affected_hosts)} affected hosts)"
        )
