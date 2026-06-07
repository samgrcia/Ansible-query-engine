from collections.abc import Iterable
from typing import Any

from ansible_query.inventory.resolver import ResolvedInventory
from ansible_query.inventory.state import InventoryState
from ansible_query.inventory.store import Store


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Ansible-style merge: dicts are merged recursively, scalars and lists are overwritten."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class Compiler:
    """Merges groupvars and hostvars per host to produce the InventoryState."""

    def __init__(self, resolved: ResolvedInventory, store: Store) -> None:
        self._resolved = resolved
        self._store = store
        self._invalidation_map: dict[str, list[str]] = self._build_invalidation_map()

    @property
    def invalidation_map(self) -> dict[str, list[str]]:
        """Maps logical source key → list of affected host names.

        Keys: 'groupvars/<group>' or 'hostvars/<host>'
        """
        return dict(self._invalidation_map)

    def compile_all(self) -> InventoryState:
        """Load and merge all vars; return a fully compiled InventoryState."""
        state = InventoryState()
        for host in self._resolved.hosts:
            state.set_vars(host, self._compile_host(host))
        return state

    def recompile(self, source_key: str, state: InventoryState) -> None:
        """Recompile hosts affected by a write to source_key."""
        for host in self._invalidation_map.get(source_key, []):
            if host in state:
                state.set_vars(host, self._compile_host(host))

    def recompile_many(self, source_keys: Iterable[str], state: InventoryState) -> None:
        """Recompile all hosts affected by any of the given source keys (deduped)."""
        affected: set[str] = set()
        for key in source_keys:
            affected.update(self._invalidation_map.get(key, []))
        for host in affected:
            if host in state:
                state.set_vars(host, self._compile_host(host))

    # ── internal ──────────────────────────────────────────────────────────────

    def _compile_host(self, hostname: str) -> dict[str, Any]:
        chain = self._resolved.host_group_chain[hostname]
        merged: dict[str, Any] = {}
        for group in chain:
            merged = deep_merge(merged, self._store.load_groupvars(group))
        return deep_merge(merged, self._store.load_hostvars(hostname))

    def _build_invalidation_map(self) -> dict[str, list[str]]:
        inv_map: dict[str, list[str]] = {}
        for host in self._resolved.hosts:
            for group in self._resolved.host_group_chain[host]:
                inv_map.setdefault(f"groupvars/{group}", []).append(host)
            inv_map.setdefault(f"hostvars/{host}", []).append(host)
        return inv_map
