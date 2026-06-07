import os
from collections.abc import Generator
from pathlib import Path

import pytest

from ansible_query.inventory.compiler import Compiler, deep_merge
from ansible_query.inventory.resolver import Resolver
from ansible_query.inventory.state import InventoryState
from ansible_query.inventory.store import Store


# ── deep_merge ────────────────────────────────────────────────────────────────

def test_deep_merge_scalar_override() -> None:
    result = deep_merge({"env": "dev", "port": 80}, {"env": "prod"})
    assert result["env"] == "prod"
    assert result["port"] == 80


def test_deep_merge_dict_recursive() -> None:
    base = {"db": {"host": "localhost", "port": 5432}}
    result = deep_merge(base, {"db": {"port": 5433}})
    assert result["db"] == {"host": "localhost", "port": 5433}


def test_deep_merge_deeply_nested() -> None:
    base = {"a": {"b": {"c": 1, "d": 2}}}
    result = deep_merge(base, {"a": {"b": {"c": 99}}})
    assert result["a"]["b"]["c"] == 99
    assert result["a"]["b"]["d"] == 2


def test_deep_merge_list_overwrites() -> None:
    result = deep_merge({"tags": ["a", "b"]}, {"tags": ["c"]})
    assert result["tags"] == ["c"]


def test_deep_merge_new_key() -> None:
    assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


def test_deep_merge_empty_base() -> None:
    assert deep_merge({}, {"k": "v"}) == {"k": "v"}


def test_deep_merge_empty_override() -> None:
    assert deep_merge({"k": "v"}, {}) == {"k": "v"}


def test_deep_merge_does_not_mutate_base() -> None:
    base = {"env": "dev"}
    deep_merge(base, {"env": "prod"})
    assert base["env"] == "dev"


def test_deep_merge_dict_replaced_by_scalar() -> None:
    result = deep_merge({"k": {"nested": 1}}, {"k": "flat"})
    assert result["k"] == "flat"


# ── Compiler setup ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def set_vault_env(vault_password_file: Path) -> Generator[None, None, None]:
    key = "ANSIBLE_VAULT_PASSWORD_FILE"
    old = os.environ.get(key)
    os.environ[key] = str(vault_password_file)
    yield
    if old is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = old


@pytest.fixture(scope="module")
def compiler_and_state(inventory_path: Path) -> tuple[Compiler, InventoryState]:
    store = Store(inventory_path)
    resolved = Resolver(inventory_path).load()
    compiler = Compiler(resolved, store)
    return compiler, compiler.compile_all()


# ── priority: groupvars inheritance ──────────────────────────────────────────

def test_groupvar_all_propagates_to_all_hosts(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    _, state = compiler_and_state
    for host in state.hosts():
        assert state.get_vars(host)["ntp_server"] == "ntp.example.com"  # type: ignore[index]


def test_hostvar_overrides_groupvar_all(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    _, state = compiler_and_state
    assert state.get_vars("node1")["env"] == "production"   # hostvars wins
    assert state.get_vars("node2")["env"] == "development"  # inherits from all


def test_child_group_overrides_parent_group(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    _, state = compiler_and_state
    # node3: chain all → app_servers → dbservers → webservers
    # dbservers sets app_role=db, webservers sets app_role=web (later → wins)
    assert state.get_vars("node3")["app_role"] == "web"


def test_multiple_groups_all_present(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    _, state = compiler_and_state
    v = state.get_vars("node1")
    assert v is not None
    assert v["deploy_user"] == "deploy"     # from app_servers
    assert v["datacenter"] == "eu-west-1"   # from europe
    assert v["http_port"] == 80             # from webservers


def test_vault_vars_included_in_state(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    _, state = compiler_and_state
    v = state.get_vars("node1")
    assert v is not None
    assert v["vault_root_password"] == "secret123"    # from hostvars/node1/node1.vault
    assert v["vault_ssl_cert"] == "my_cert_content"   # from groupvars/webservers/webservers.vault


def test_all_hosts_compiled(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    _, state = compiler_and_state
    assert sorted(state.hosts()) == sorted([
        "node1", "node2", "node3", "node4",
        "mon01", "mon02", "standalone",
    ])


# ── invalidation_map ──────────────────────────────────────────────────────────

def test_invalidation_map_all_covers_every_host(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    compiler, state = compiler_and_state
    assert set(compiler.invalidation_map["groupvars/all"]) == set(state.hosts())


def test_invalidation_map_webservers_subset(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    compiler, _ = compiler_and_state
    assert set(compiler.invalidation_map["groupvars/webservers"]) == {"node1", "node2", "node3"}


def test_invalidation_map_hostvars_single(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    compiler, _ = compiler_and_state
    assert compiler.invalidation_map["hostvars/node1"] == ["node1"]
    assert compiler.invalidation_map["hostvars/standalone"] == ["standalone"]


def test_invalidation_map_dbservers(
    compiler_and_state: tuple[Compiler, InventoryState],
) -> None:
    compiler, _ = compiler_and_state
    assert set(compiler.invalidation_map["groupvars/dbservers"]) == {"node3", "node4"}


# ── recompile ─────────────────────────────────────────────────────────────────

def test_recompile_after_groupvar_change(inventory_path: Path) -> None:
    store = Store(inventory_path)
    resolved = Resolver(inventory_path).load()
    compiler = Compiler(resolved, store)
    state = compiler.compile_all()

    monitoring_yaml = inventory_path / "groupvars" / "monitoring" / "monitoring.yaml"
    cm = store.load_yaml_raw(monitoring_yaml)
    cm["check_interval"] = 999
    store.write_yaml(monitoring_yaml, cm)

    try:
        compiler.recompile("groupvars/monitoring", state)
        assert state.get_vars("mon01")["check_interval"] == 999
        assert state.get_vars("mon02")["check_interval"] == 999
        node1_vars = state.get_vars("node1")
        assert node1_vars is not None
        assert "check_interval" not in node1_vars
    finally:
        cm2 = store.load_yaml_raw(monitoring_yaml)
        cm2["check_interval"] = 60
        store.write_yaml(monitoring_yaml, cm2)


def test_recompile_does_not_affect_other_hosts(inventory_path: Path) -> None:
    store = Store(inventory_path)
    resolved = Resolver(inventory_path).load()
    compiler = Compiler(resolved, store)
    state = compiler.compile_all()

    state.update_var("node1", "ansible_host", "STALE")

    # Recompiling monitoring group must not touch node1
    compiler.recompile("groupvars/monitoring", state)
    assert state.get_vars("node1")["ansible_host"] == "STALE"

    # Recompiling hostvars/node1 restores it
    compiler.recompile("hostvars/node1", state)
    assert state.get_vars("node1")["ansible_host"] == "10.0.0.1"


def test_recompile_many_deduplicates(inventory_path: Path) -> None:
    store = Store(inventory_path)
    resolved = Resolver(inventory_path).load()
    compiler = Compiler(resolved, store)
    state = compiler.compile_all()

    state.update_var("node1", "ansible_host", "STALE")
    state.update_var("mon01", "ansible_host", "STALE")

    compiler.recompile_many(["hostvars/node1", "hostvars/mon01"], state)
    assert state.get_vars("node1")["ansible_host"] == "10.0.0.1"
    assert state.get_vars("mon01")["ansible_host"] == "10.0.1.1"
