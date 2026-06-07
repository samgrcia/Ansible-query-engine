import fnmatch
import shutil
from pathlib import Path
from typing import Any

from ansible_query.inventory.bulk import BulkBuffer
from ansible_query.inventory.compiler import Compiler, deep_merge
from ansible_query.inventory.resolver import ResolvedInventory, Resolver
from ansible_query.inventory.state import InventoryState
from ansible_query.inventory.store import Store
from ansible_query.parser.ast import (
    CreateHostQuery,
    DropHostQuery,
    Query,
    RemoveHostQuery,
    SelectQuery,
    SetQuery,
    UnsetQuery,
)
from ansible_query.parser.parser import parse


class QueryError(Exception):
    pass


class QueryEngine:
    def __init__(self, inventory_path: Path | str) -> None:
        self._path = Path(inventory_path)
        self._store: Store
        self._compiler: Compiler
        self._resolved: ResolvedInventory
        self._state: InventoryState
        self._reload()

    def execute(self, query: str) -> Any:
        ast = parse(query)
        if isinstance(ast, SelectQuery):
            return self._handle_select(ast)
        if isinstance(ast, SetQuery):
            return self._handle_set(ast)
        if isinstance(ast, UnsetQuery):
            return self._handle_unset(ast)
        if isinstance(ast, CreateHostQuery):
            return self._handle_create_host(ast)
        if isinstance(ast, RemoveHostQuery):
            return self._handle_remove_host(ast)
        if isinstance(ast, DropHostQuery):
            return self._handle_drop_host(ast)
        raise QueryError(f"Unknown query type: {type(ast).__name__}")  # pragma: no cover

    def execute_bulk(self, queries: list[str]) -> None:
        """Phase 1: validate + stage all writes in BulkBuffer; Phase 2: atomic flush."""
        asts: list[Query] = []
        for q in queries:
            ast = parse(q)
            if isinstance(ast, (CreateHostQuery, RemoveHostQuery, DropHostQuery)):
                raise QueryError(
                    f"{type(ast).__name__} is not supported in bulk mode"
                )
            if isinstance(ast, SelectQuery):
                raise QueryError("SELECT is not supported in bulk mode")
            asts.append(ast)

        buf = BulkBuffer()
        source_keys: set[str] = set()

        try:
            for ast in asts:
                if isinstance(ast, SetQuery):
                    self._bulk_set(ast, buf, source_keys)
                else:
                    assert isinstance(ast, UnsetQuery)
                    self._bulk_unset(ast, buf, source_keys)
        except Exception:
            buf.clear()
            raise

        if buf.is_empty():
            return

        self._store.flush_bulk(buf.pending_files)
        self._compiler.recompile_many(source_keys, self._state)

    # ── SELECT ────────────────────────────────────────────────────────────────

    def _handle_select(self, q: SelectQuery) -> Any:
        if q.condition.kind == "host":
            hosts = self._state.match_hosts(q.condition.pattern)
            if not hosts:
                return {}
            results: dict[str, Any] = {}
            for host in hosts:
                raw = self._fetch_host_view(host, q.sources)
                results[host] = _filter_columns(raw, q.columns)
            if len(hosts) == 1:
                single = results[hosts[0]]
                if q.columns != ["*"] and len(q.columns) == 1:
                    return single.get(q.columns[0])
                return single
            return results
        else:
            groups = self._match_groups(q.condition.pattern)
            if not groups:
                return {}
            results = {}
            for group in groups:
                raw = dict(self._store.load_groupvars(group))
                results[group] = _filter_columns(raw, q.columns)
            if len(groups) == 1:
                single = results[groups[0]]
                if q.columns != ["*"] and len(q.columns) == 1:
                    return single.get(q.columns[0])
                return single
            return results

    def _fetch_host_view(self, host: str, sources: list[str]) -> dict[str, Any]:
        if "hostvars" in sources and "groupvars" in sources:
            return dict(self._state.get_vars(host) or {})
        if "hostvars" in sources:
            return dict(self._store.load_hostvars(host))
        chain = self._resolved.host_group_chain.get(host, [])
        merged: dict[str, Any] = {}
        for group in chain:
            merged = deep_merge(merged, self._store.load_groupvars(group))
        return merged

    # ── SET ───────────────────────────────────────────────────────────────────

    def _handle_set(self, q: SetQuery) -> None:
        _check_vault_prefix(q.variable, q.encrypt)
        if q.condition.kind == "host":
            hosts = self._state.match_hosts(q.condition.pattern)
            if not hosts:
                raise QueryError(f"No hosts matched: {q.condition.pattern!r}")
            for host in hosts:
                self._set_host_var(host, q.variable, q.value, q.encrypt)
        else:
            groups = self._match_groups(q.condition.pattern)
            if not groups:
                raise QueryError(f"No groups matched: {q.condition.pattern!r}")
            for group in groups:
                self._set_group_var(group, q.variable, q.value, q.encrypt)

    def _set_host_var(self, host: str, variable: str, value: Any, encrypt: bool) -> None:
        if encrypt:
            var_name = variable if variable.startswith("vault_") else f"vault_{variable}"
            path = self._path / "hostvars" / host / f"{host}.vault"
            data = self._store.load_vault_raw(path)
            data[var_name] = value
            self._store.write_vault(path, data)
        else:
            path = self._path / "hostvars" / host / f"{host}.yaml"
            data = self._store.load_yaml_raw(path)
            data[variable] = value
            self._store.write_yaml(path, data)
        self._compiler.recompile(f"hostvars/{host}", self._state)

    def _set_group_var(self, group: str, variable: str, value: Any, encrypt: bool) -> None:
        if encrypt:
            var_name = variable if variable.startswith("vault_") else f"vault_{variable}"
            path = self._path / "groupvars" / group / f"{group}.vault"
            data = self._store.load_vault_raw(path)
            data[var_name] = value
            self._store.write_vault(path, data)
        else:
            path = self._path / "groupvars" / group / f"{group}.yaml"
            data = self._store.load_yaml_raw(path)
            data[variable] = value
            self._store.write_yaml(path, data)
        self._compiler.recompile(f"groupvars/{group}", self._state)

    # ── UNSET ─────────────────────────────────────────────────────────────────

    def _handle_unset(self, q: UnsetQuery) -> None:
        if q.condition.kind == "host":
            hosts = self._state.match_hosts(q.condition.pattern)
            if not hosts:
                raise QueryError(f"No hosts matched: {q.condition.pattern!r}")
            if q.source != "hostvars":
                raise QueryError("UNSET FROM groupvars requires WHERE group = ...")
            for host in hosts:
                self._unset_from_hostfile(host, q.variable)
        else:
            groups = self._match_groups(q.condition.pattern)
            if not groups:
                raise QueryError(f"No groups matched: {q.condition.pattern!r}")
            if q.source != "groupvars":
                raise QueryError("UNSET FROM hostvars requires WHERE host = ...")
            for group in groups:
                self._unset_from_groupfile(group, q.variable)

    def _unset_from_hostfile(self, host: str, variable: str) -> None:
        is_vault = variable.startswith("vault_")
        if is_vault:
            path = self._path / "hostvars" / host / f"{host}.vault"
            data = self._store.load_vault_raw(path)
        else:
            path = self._path / "hostvars" / host / f"{host}.yaml"
            data = self._store.load_yaml_raw(path)
        if variable not in data:
            raise QueryError(f"Variable {variable!r} not found for host {host!r}")
        del data[variable]
        if is_vault:
            self._store.write_vault(path, data)
        else:
            self._store.write_yaml(path, data)
        self._compiler.recompile(f"hostvars/{host}", self._state)

    def _unset_from_groupfile(self, group: str, variable: str) -> None:
        is_vault = variable.startswith("vault_")
        if is_vault:
            path = self._path / "groupvars" / group / f"{group}.vault"
            data = self._store.load_vault_raw(path)
        else:
            path = self._path / "groupvars" / group / f"{group}.yaml"
            data = self._store.load_yaml_raw(path)
        if variable not in data:
            raise QueryError(f"Variable {variable!r} not found for group {group!r}")
        del data[variable]
        if is_vault:
            self._store.write_vault(path, data)
        else:
            self._store.write_yaml(path, data)
        self._compiler.recompile(f"groupvars/{group}", self._state)

    # ── CREATE HOST ───────────────────────────────────────────────────────────

    def _handle_create_host(self, q: CreateHostQuery) -> None:
        if q.host in self._state:
            raise QueryError(f"Host {q.host!r} already exists")
        nodes_path = self._path / "nodes.yaml"
        nodes = self._store.load_yaml_raw(nodes_path)
        target_groups = q.groups if q.groups else ["ungrouped"]
        for group in target_groups:
            group_raw = nodes.get(group)
            if group_raw is None:
                nodes[group] = {"hosts": {q.host: None}}
            else:
                hosts_section = group_raw.get("hosts")
                if hosts_section is None:
                    group_raw["hosts"] = {q.host: None}
                else:
                    hosts_section[q.host] = None
        self._store.write_yaml(nodes_path, nodes)
        self._reload()

    # ── REMOVE HOST ───────────────────────────────────────────────────────────

    def _handle_remove_host(self, q: RemoveHostQuery) -> None:
        nodes_path = self._path / "nodes.yaml"
        nodes = self._store.load_yaml_raw(nodes_path)
        for group in q.groups:
            group_raw = nodes.get(group)
            if group_raw is None:
                raise QueryError(f"Group {group!r} not found in nodes.yaml")
            hosts_section = group_raw.get("hosts")
            if hosts_section is None or q.host not in hosts_section:
                raise QueryError(
                    f"Host {q.host!r} not directly listed in group {group!r}"
                )
            del hosts_section[q.host]
        self._store.write_yaml(nodes_path, nodes)
        self._reload()

    # ── DROP HOST ─────────────────────────────────────────────────────────────

    def _handle_drop_host(self, q: DropHostQuery) -> None:
        if q.host not in self._state:
            raise QueryError(f"Host {q.host!r} does not exist")
        nodes_path = self._path / "nodes.yaml"
        nodes = self._store.load_yaml_raw(nodes_path)
        for group_name in list(nodes.keys()):
            group_raw = nodes[group_name]
            if group_raw is None:
                continue
            hosts_section = group_raw.get("hosts")
            if hosts_section is not None and q.host in hosts_section:
                del hosts_section[q.host]
        self._store.write_yaml(nodes_path, nodes)
        if not q.keep_vars:
            hostvar_dir = self._path / "hostvars" / q.host
            if hostvar_dir.exists():
                shutil.rmtree(hostvar_dir)
        self._reload()

    # ── bulk helpers ──────────────────────────────────────────────────────────

    def _bulk_set(self, q: SetQuery, buf: BulkBuffer, source_keys: set[str]) -> None:
        _check_vault_prefix(q.variable, q.encrypt)
        if q.condition.kind == "host":
            hosts = self._state.match_hosts(q.condition.pattern)
            if not hosts:
                raise QueryError(f"No hosts matched: {q.condition.pattern!r}")
            for host in hosts:
                if q.encrypt:
                    var_name = q.variable if q.variable.startswith("vault_") else f"vault_{q.variable}"
                    path = self._path / "hostvars" / host / f"{host}.vault"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else self._store.load_vault_raw(path)
                    data[var_name] = q.value
                else:
                    path = self._path / "hostvars" / host / f"{host}.yaml"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else dict(self._store.load_yaml_raw(path))
                    data[q.variable] = q.value
                buf.stage_file(path, data, [host])
                source_keys.add(f"hostvars/{host}")
        else:
            groups = self._match_groups(q.condition.pattern)
            if not groups:
                raise QueryError(f"No groups matched: {q.condition.pattern!r}")
            for group in groups:
                if q.encrypt:
                    var_name = q.variable if q.variable.startswith("vault_") else f"vault_{q.variable}"
                    path = self._path / "groupvars" / group / f"{group}.vault"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else self._store.load_vault_raw(path)
                    data[var_name] = q.value
                else:
                    path = self._path / "groupvars" / group / f"{group}.yaml"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else dict(self._store.load_yaml_raw(path))
                    data[q.variable] = q.value
                buf.stage_file(path, data)
                source_keys.add(f"groupvars/{group}")

    def _bulk_unset(self, q: UnsetQuery, buf: BulkBuffer, source_keys: set[str]) -> None:
        if q.condition.kind == "host":
            hosts = self._state.match_hosts(q.condition.pattern)
            if not hosts:
                raise QueryError(f"No hosts matched: {q.condition.pattern!r}")
            if q.source != "hostvars":
                raise QueryError("UNSET FROM groupvars requires WHERE group = ...")
            is_vault = q.variable.startswith("vault_")
            for host in hosts:
                if is_vault:
                    path = self._path / "hostvars" / host / f"{host}.vault"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else self._store.load_vault_raw(path)
                else:
                    path = self._path / "hostvars" / host / f"{host}.yaml"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else dict(self._store.load_yaml_raw(path))
                if q.variable not in data:
                    raise QueryError(f"Variable {q.variable!r} not found for host {host!r}")
                del data[q.variable]
                buf.stage_file(path, data, [host])
                source_keys.add(f"hostvars/{host}")
        else:
            groups = self._match_groups(q.condition.pattern)
            if not groups:
                raise QueryError(f"No groups matched: {q.condition.pattern!r}")
            if q.source != "groupvars":
                raise QueryError("UNSET FROM hostvars requires WHERE host = ...")
            is_vault = q.variable.startswith("vault_")
            for group in groups:
                if is_vault:
                    path = self._path / "groupvars" / group / f"{group}.vault"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else self._store.load_vault_raw(path)
                else:
                    path = self._path / "groupvars" / group / f"{group}.yaml"
                    staged = buf.get_staged(path)
                    data = dict(staged) if staged is not None else dict(self._store.load_yaml_raw(path))
                if q.variable not in data:
                    raise QueryError(f"Variable {q.variable!r} not found for group {group!r}")
                del data[q.variable]
                buf.stage_file(path, data)
                source_keys.add(f"groupvars/{group}")

    # ── public query helpers ──────────────────────────────────────────────────

    def match_hosts(self, pattern: str) -> list[str]:
        return self._state.match_hosts(pattern)

    def match_groups(self, pattern: str) -> list[str]:
        return self._match_groups(pattern)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        self._resolved = Resolver(self._path).load()
        self._store = Store(self._path)
        self._compiler = Compiler(self._resolved, self._store)
        self._state = self._compiler.compile_all()

    def _match_groups(self, pattern: str) -> list[str]:
        known = set(self._resolved.group_hosts.keys()) | {"all"}
        return [g for g in sorted(known) if fnmatch.fnmatch(g, pattern)]


def _check_vault_prefix(variable: str, encrypt: bool) -> None:
    if not encrypt and variable.startswith("vault_"):
        raise QueryError(
            f"Variable {variable!r} has 'vault_' prefix; add ENCRYPT or rename it"
        )


def _filter_columns(data: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    if columns == ["*"]:
        return data
    return {c: data[c] for c in columns if c in data}
