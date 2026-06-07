from pathlib import Path

import pytest

from ansible_query.inventory.resolver import ResolvedInventory, Resolver, expand_range

# ── expand_range ──────────────────────────────────────────────────────────────

def test_expand_range_simple():
    assert expand_range("node[1:3]") == ["node1", "node2", "node3"]


def test_expand_range_zero_padded():
    assert expand_range("mon[01:03]") == ["mon01", "mon02", "mon03"]


def test_expand_range_no_range():
    assert expand_range("node1") == ["node1"]


def test_expand_range_with_suffix():
    assert expand_range("web[1:2]-srv") == ["web1-srv", "web2-srv"]


def test_expand_range_single():
    assert expand_range("node[5:5]") == ["node5"]


def test_expand_range_zero_padded_crossover():
    result = expand_range("web[01:10]")
    assert result == [
        "web01", "web02", "web03", "web04", "web05",
        "web06", "web07", "web08", "web09", "web10",
    ]


# ── Resolver fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def resolved(inventory_path: Path) -> ResolvedInventory:
    return Resolver(inventory_path).load()


# ── host discovery ────────────────────────────────────────────────────────────

def test_all_hosts_present(resolved: ResolvedInventory) -> None:
    assert sorted(resolved.hosts) == sorted([
        "node1", "node2", "node3", "node4",
        "mon01", "mon02", "standalone",
    ])


def test_range_expansion_in_load(resolved: ResolvedInventory) -> None:
    assert sorted(resolved.group_hosts["webservers"]) == ["node1", "node2", "node3"]


def test_zero_padded_range_in_load(resolved: ResolvedInventory) -> None:
    assert sorted(resolved.group_hosts["monitoring"]) == ["mon01", "mon02"]


def test_group_children(resolved: ResolvedInventory) -> None:
    assert sorted(resolved.group_children["app_servers"]) == ["dbservers", "webservers"]


# ── group chains ──────────────────────────────────────────────────────────────

def test_all_chains_start_with_all(resolved: ResolvedInventory) -> None:
    for host in resolved.hosts:
        assert resolved.host_group_chain[host][0] == "all"


def test_group_chain_node1(resolved: ResolvedInventory) -> None:
    chain = resolved.host_group_chain["node1"]
    # node1 ∈ webservers (range) + europe; webservers is child of app_servers
    assert chain[0] == "all"
    assert "app_servers" in chain
    assert "webservers" in chain
    assert "europe" in chain
    assert chain.index("app_servers") < chain.index("webservers")


def test_group_chain_sibling_order_alphabetical(resolved: ResolvedInventory) -> None:
    # node3 ∈ webservers (range) + dbservers; both children of app_servers → alphabetical
    chain = resolved.host_group_chain["node3"]
    assert chain.index("app_servers") < chain.index("dbservers")
    assert chain.index("app_servers") < chain.index("webservers")
    assert chain.index("dbservers") < chain.index("webservers")


def test_group_chain_standalone_ungrouped(resolved: ResolvedInventory) -> None:
    assert resolved.host_group_chain["standalone"] == ["all", "ungrouped"]


def test_group_chain_monitoring(resolved: ResolvedInventory) -> None:
    assert resolved.host_group_chain["mon01"] == ["all", "monitoring"]


def test_group_chain_has_no_duplicates(resolved: ResolvedInventory) -> None:
    for host, chain in resolved.host_group_chain.items():
        assert len(chain) == len(set(chain)), f"duplicate in chain for {host}: {chain}"
