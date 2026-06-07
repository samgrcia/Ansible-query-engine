import fnmatch
from typing import Any


class InventoryState:
    """Compiled in-memory inventory: { hostname: { var: value } }."""

    def __init__(self, data: dict[str, dict[str, Any]] | None = None) -> None:
        self._data: dict[str, dict[str, Any]] = data if data is not None else {}

    # ── read ──────────────────────────────────────────────────────────────────

    def hosts(self) -> list[str]:
        return list(self._data)

    def get_vars(self, hostname: str) -> dict[str, Any] | None:
        return self._data.get(hostname)

    def match_hosts(self, pattern: str) -> list[str]:
        """Return hostnames matching a shell-style wildcard pattern."""
        return [h for h in self._data if fnmatch.fnmatch(h, pattern)]

    # ── write ─────────────────────────────────────────────────────────────────

    def set_vars(self, hostname: str, vars: dict[str, Any]) -> None:
        """Replace the entire var dict for a host (used by Compiler)."""
        self._data[hostname] = vars

    def update_var(self, hostname: str, key: str, value: Any) -> None:
        if hostname not in self._data:
            raise KeyError(f"unknown host: {hostname!r}")
        self._data[hostname][key] = value

    def delete_var(self, hostname: str, key: str) -> None:
        if hostname not in self._data:
            raise KeyError(f"unknown host: {hostname!r}")
        if key not in self._data[hostname]:
            raise KeyError(f"variable not found: {key!r} on host {hostname!r}")
        del self._data[hostname][key]

    def add_host(self, hostname: str) -> None:
        self._data.setdefault(hostname, {})

    def remove_host(self, hostname: str) -> None:
        self._data.pop(hostname, None)

    # ── dunder ────────────────────────────────────────────────────────────────

    def __contains__(self, item: object) -> bool:
        return item in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"InventoryState({len(self._data)} hosts)"
