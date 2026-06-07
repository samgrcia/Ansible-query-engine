import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_RANGE_RE = re.compile(r"^(.*)\[(\d+):(\d+)\](.*)$")


@dataclass
class ResolvedInventory:
    hosts: list[str]
    group_hosts: dict[str, list[str]]       # group → direct expanded hosts
    group_children: dict[str, list[str]]    # group → direct child groups
    host_group_chain: dict[str, list[str]]  # host → [all, ..., most_specific]


def expand_range(pattern: str) -> list[str]:
    """Expand Ansible range syntax: node[1:3] → [node1, node2, node3]."""
    m = _RANGE_RE.match(pattern)
    if not m:
        return [pattern]
    prefix, start_s, end_s, suffix = m.groups()
    start, end = int(start_s), int(end_s)
    width = len(start_s) if start_s.startswith("0") else 0
    return [
        f"{prefix}{str(i).zfill(width) if width else i}{suffix}"
        for i in range(start, end + 1)
    ]


class Resolver:
    def __init__(self, inventory_path: Path) -> None:
        self._path = inventory_path
        self._yaml = YAML()

    def load(self) -> ResolvedInventory:
        nodes_file = self._path / "nodes.yaml"
        with nodes_file.open() as f:
            raw: dict[str, Any] = self._yaml.load(f) or {}

        group_hosts: dict[str, list[str]] = {}
        group_children: dict[str, list[str]] = {}

        for group_name, group_data in raw.items():
            gd: dict[str, Any] = dict(group_data or {})
            expanded: list[str] = []
            for pattern in (gd.get("hosts") or {}):
                expanded.extend(expand_range(str(pattern)))
            group_hosts[group_name] = expanded
            group_children[group_name] = list((gd.get("children") or {}).keys())

        # parent_of[child] = [parent, ...]
        parent_of: dict[str, list[str]] = {}
        for parent, children in group_children.items():
            for child in children:
                parent_of.setdefault(child, []).append(parent)

        # host → set of groups it directly belongs to
        host_direct: dict[str, set[str]] = {}
        for group, hosts in group_hosts.items():
            for host in hosts:
                host_direct.setdefault(host, set()).add(group)

        all_hosts = sorted(host_direct.keys())
        host_group_chain = {
            host: _group_chain(host_direct[host], group_children, parent_of)
            for host in all_hosts
        }

        return ResolvedInventory(
            hosts=all_hosts,
            group_hosts=group_hosts,
            group_children=group_children,
            host_group_chain=host_group_chain,
        )


def _group_chain(
    direct: set[str],
    group_children: dict[str, list[str]],
    parent_of: dict[str, list[str]],
) -> list[str]:
    """Return groups in priority order: 'all' first, most specific last."""
    # Walk up from direct groups to collect all ancestor groups
    host_groups: set[str] = set()
    stack = list(direct)
    while stack:
        g = stack.pop()
        if g in host_groups:
            continue
        host_groups.add(g)
        for parent in parent_of.get(g, []):
            if parent not in host_groups:
                stack.append(parent)
    host_groups.add("all")

    # Build directed edges within the host's group subgraph
    edges: dict[str, list[str]] = {g: [] for g in host_groups}
    in_degree: dict[str, int] = {g: 0 for g in host_groups}

    for parent in host_groups:
        if parent == "all":
            continue
        for child in group_children.get(parent, []):
            if child in host_groups:
                edges[parent].append(child)
                in_degree[child] += 1

    # Groups with no explicit parent become direct children of 'all'
    for g in host_groups:
        if g != "all" and in_degree[g] == 0:
            edges["all"].append(g)
            in_degree[g] = 1

    # Kahn's algorithm with alphabetical tie-breaking for siblings
    queue: list[str] = ["all"]
    result: list[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for child in sorted(edges.get(node, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
        queue.sort()

    return result
