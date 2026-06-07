import pytest

from ansible_query.inventory.state import InventoryState


def test_add_and_get():
    state = InventoryState()
    state.add_host("node1")
    state.update_var("node1", "env", "production")
    assert state.get_vars("node1") == {"env": "production"}


def test_set_vars_replaces_entirely():
    state = InventoryState({"node1": {"a": 1, "b": 2}})
    state.set_vars("node1", {"c": 3})
    assert state.get_vars("node1") == {"c": 3}


def test_update_var_unknown_host():
    state = InventoryState()
    with pytest.raises(KeyError, match="unknown host"):
        state.update_var("ghost", "env", "x")


def test_delete_var():
    state = InventoryState({"node1": {"env": "prod", "ntp": "ntp.example.com"}})
    state.delete_var("node1", "ntp")
    assert state.get_vars("node1") == {"env": "prod"}


def test_delete_var_missing_raises():
    state = InventoryState({"node1": {}})
    with pytest.raises(KeyError, match="variable not found"):
        state.delete_var("node1", "nonexistent")


def test_delete_var_unknown_host():
    state = InventoryState()
    with pytest.raises(KeyError, match="unknown host"):
        state.delete_var("ghost", "env")


def test_remove_host():
    state = InventoryState({"node1": {"env": "prod"}})
    state.remove_host("node1")
    assert "node1" not in state
    assert len(state) == 0


def test_remove_host_idempotent():
    state = InventoryState()
    state.remove_host("ghost")  # must not raise


def test_match_hosts_wildcard():
    state = InventoryState({"node1": {}, "node2": {}, "web1": {}})
    assert sorted(state.match_hosts("node*")) == ["node1", "node2"]


def test_match_hosts_exact():
    state = InventoryState({"node1": {}, "node2": {}})
    assert state.match_hosts("node1") == ["node1"]


def test_match_hosts_all():
    state = InventoryState({"node1": {}, "node2": {}, "web1": {}})
    assert sorted(state.match_hosts("*")) == ["node1", "node2", "web1"]


def test_match_hosts_no_match():
    state = InventoryState({"node1": {}})
    assert state.match_hosts("db*") == []


def test_contains_and_len():
    state = InventoryState({"node1": {}, "node2": {}})
    assert "node1" in state
    assert "ghost" not in state
    assert len(state) == 2


def test_hosts_returns_all():
    state = InventoryState({"node1": {}, "node2": {}})
    assert sorted(state.hosts()) == ["node1", "node2"]


def test_get_vars_unknown_returns_none():
    state = InventoryState()
    assert state.get_vars("ghost") is None
