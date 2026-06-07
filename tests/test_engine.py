import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from ansible_query.engine import QueryEngine, QueryError


@pytest.fixture
def fresh_inv(
    tmp_path: Path,
    inventory_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    inv = tmp_path / "inventory"
    shutil.copytree(inventory_path, inv)
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    return inv


@pytest.fixture
def engine(fresh_inv: Path) -> QueryEngine:
    return QueryEngine(fresh_inv)


# ── SELECT ────────────────────────────────────────────────────────────────────

def test_select_all_hostvars(engine: QueryEngine) -> None:
    result = engine.execute('SELECT * FROM hostvars WHERE host = "node1"')
    assert result["ansible_host"] == "10.0.0.1"
    assert result["env"] == "production"
    assert result["vault_root_password"] == "secret123"


def test_select_specific_column_returns_scalar(engine: QueryEngine) -> None:
    result = engine.execute('SELECT env FROM hostvars WHERE host = "node1"')
    assert result == "production"


def test_select_multiple_columns_returns_dict(engine: QueryEngine) -> None:
    result = engine.execute('SELECT env, ansible_host FROM hostvars WHERE host = "node1"')
    assert result == {"env": "production", "ansible_host": "10.0.0.1"}


def test_select_compiled_both_sources(engine: QueryEngine) -> None:
    result = engine.execute('SELECT * FROM hostvars, groupvars WHERE host = "node1"')
    # hostvars override group vars
    assert result["env"] == "production"
    # inherited from webservers
    assert result["http_port"] == 80
    assert result["app_role"] == "web"
    # inherited from europe
    assert result["datacenter"] == "eu-west-1"
    # inherited from app_servers
    assert result["deploy_user"] == "deploy"
    # vault var
    assert result["vault_root_password"] == "secret123"


def test_select_groupvars_by_group(engine: QueryEngine) -> None:
    result = engine.execute('SELECT * FROM groupvars WHERE group = "webservers"')
    assert result["http_port"] == 80
    assert result["app_role"] == "web"
    assert result["vault_ssl_cert"] == "my_cert_content"


def test_select_wildcard_host(engine: QueryEngine) -> None:
    result = engine.execute('SELECT ansible_host FROM hostvars WHERE host = "node*"')
    assert isinstance(result, dict)
    assert result["node1"]["ansible_host"] == "10.0.0.1"
    assert result["node4"]["ansible_host"] == "10.0.0.4"


def test_select_no_match_returns_empty(engine: QueryEngine) -> None:
    result = engine.execute('SELECT * FROM hostvars WHERE host = "ghost"')
    assert result == {}


def test_select_group_no_match_returns_empty(engine: QueryEngine) -> None:
    result = engine.execute('SELECT * FROM groupvars WHERE group = "nonexistent"')
    assert result == {}


# ── SET ───────────────────────────────────────────────────────────────────────

def test_set_host_var_persists(engine: QueryEngine) -> None:
    engine.execute('SET env = "staging" WHERE host = "node1"')
    assert engine.execute('SELECT env FROM hostvars WHERE host = "node1"') == "staging"


def test_set_host_var_reflects_in_compiled(engine: QueryEngine) -> None:
    engine.execute('SET new_var = "hello" WHERE host = "node2"')
    compiled = engine.execute('SELECT * FROM hostvars, groupvars WHERE host = "node2"')
    assert compiled["new_var"] == "hello"


def test_set_group_var_propagates(engine: QueryEngine) -> None:
    engine.execute('SET http_port = 8080 WHERE group = "webservers"')
    assert engine.execute('SELECT http_port FROM groupvars WHERE group = "webservers"') == 8080
    compiled = engine.execute('SELECT * FROM hostvars, groupvars WHERE host = "node1"')
    assert compiled["http_port"] == 8080


def test_set_encrypt_adds_vault_prefix(engine: QueryEngine) -> None:
    engine.execute('SET db_pass = "secret" WHERE host = "node4" ENCRYPT')
    hostvars = engine.execute('SELECT * FROM hostvars WHERE host = "node4"')
    assert hostvars.get("vault_db_pass") == "secret"


def test_set_encrypt_existing_vault_prefix_kept(engine: QueryEngine) -> None:
    engine.execute('SET vault_key = "xyz" WHERE host = "node2" ENCRYPT')
    hostvars = engine.execute('SELECT * FROM hostvars WHERE host = "node2"')
    assert hostvars.get("vault_key") == "xyz"
    assert "vault_vault_key" not in hostvars


def test_set_vault_prefix_without_encrypt_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError, match="vault_"):
        engine.execute('SET vault_x = "pw" WHERE host = "node1"')


def test_set_wildcard_updates_multiple_hosts(engine: QueryEngine) -> None:
    engine.execute('SET batch_var = "yes" WHERE host = "mon*"')
    assert engine.execute('SELECT batch_var FROM hostvars WHERE host = "mon01"') == "yes"
    assert engine.execute('SELECT batch_var FROM hostvars WHERE host = "mon02"') == "yes"


def test_set_no_match_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError, match="No hosts matched"):
        engine.execute('SET env = "x" WHERE host = "ghost"')


# ── UNSET ─────────────────────────────────────────────────────────────────────

def test_unset_hostvars_var(engine: QueryEngine) -> None:
    engine.execute('UNSET env FROM hostvars WHERE host = "node1"')
    hostvars = engine.execute('SELECT * FROM hostvars WHERE host = "node1"')
    assert "env" not in hostvars


def test_unset_falls_back_to_group_var(engine: QueryEngine) -> None:
    # node1 hostvars overrides all.env; after unset, compiled falls back
    engine.execute('UNSET env FROM hostvars WHERE host = "node1"')
    compiled = engine.execute('SELECT * FROM hostvars, groupvars WHERE host = "node1"')
    assert compiled.get("env") == "development"


def test_unset_groupvars_var(engine: QueryEngine) -> None:
    engine.execute('UNSET http_port FROM groupvars WHERE group = "webservers"')
    gvars = engine.execute('SELECT * FROM groupvars WHERE group = "webservers"')
    assert "http_port" not in gvars


def test_unset_vault_var(engine: QueryEngine) -> None:
    engine.execute('UNSET vault_root_password FROM hostvars WHERE host = "node1"')
    hostvars = engine.execute('SELECT * FROM hostvars WHERE host = "node1"')
    assert "vault_root_password" not in hostvars


def test_unset_missing_var_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError, match="not found"):
        engine.execute('UNSET nonexistent_var FROM hostvars WHERE host = "node1"')


# ── CREATE HOST ───────────────────────────────────────────────────────────────

def test_create_host_in_group(engine: QueryEngine) -> None:
    engine.execute("CREATE HOST newhost IN GROUPS webservers")
    compiled = engine.execute('SELECT * FROM hostvars, groupvars WHERE host = "newhost"')
    assert compiled.get("http_port") == 80
    assert compiled.get("app_role") == "web"


def test_create_host_multiple_groups(engine: QueryEngine) -> None:
    engine.execute("CREATE HOST newhost IN GROUPS webservers, europe")
    compiled = engine.execute('SELECT * FROM hostvars, groupvars WHERE host = "newhost"')
    assert compiled.get("http_port") == 80
    assert compiled.get("datacenter") == "eu-west-1"


def test_create_host_no_groups_goes_to_ungrouped(engine: QueryEngine, fresh_inv: Path) -> None:
    engine.execute("CREATE HOST solo")
    yaml = YAML()
    with (fresh_inv / "nodes.yaml").open() as f:
        nodes = yaml.load(f)
    assert "solo" in nodes["ungrouped"]["hosts"]


def test_create_host_can_receive_vars(engine: QueryEngine) -> None:
    engine.execute("CREATE HOST newhost IN GROUPS webservers")
    engine.execute('SET tag = "created" WHERE host = "newhost"')
    assert engine.execute('SELECT tag FROM hostvars WHERE host = "newhost"') == "created"


def test_create_host_already_exists_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError, match="already exists"):
        engine.execute("CREATE HOST node1")


# ── REMOVE HOST ───────────────────────────────────────────────────────────────

def test_remove_host_from_group(engine: QueryEngine) -> None:
    engine.execute("CREATE HOST temphost IN GROUPS europe")
    engine.execute("REMOVE HOST temphost FROM GROUPS europe")
    assert engine.execute('SELECT * FROM hostvars WHERE host = "temphost"') == {}


def test_remove_host_not_in_group_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError):
        engine.execute("REMOVE HOST node1 FROM GROUPS dbservers")


def test_remove_host_nonexistent_group_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError, match="not found"):
        engine.execute("REMOVE HOST node1 FROM GROUPS no_such_group")


# ── DROP HOST ─────────────────────────────────────────────────────────────────

def test_drop_host_removes_from_state(engine: QueryEngine, fresh_inv: Path) -> None:
    engine.execute("DROP HOST standalone")
    assert engine.execute('SELECT * FROM hostvars WHERE host = "standalone"') == {}


def test_drop_host_deletes_hostvars_dir(engine: QueryEngine, fresh_inv: Path) -> None:
    engine.execute("DROP HOST standalone")
    assert not (fresh_inv / "hostvars" / "standalone").exists()


def test_drop_host_keep_vars_preserves_files(engine: QueryEngine, fresh_inv: Path) -> None:
    engine.execute("DROP HOST standalone KEEP VARS")
    assert (fresh_inv / "hostvars" / "standalone").exists()
    assert engine.execute('SELECT * FROM hostvars WHERE host = "standalone"') == {}


def test_drop_host_not_exists_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError, match="does not exist"):
        engine.execute("DROP HOST ghost")


# ── execute_bulk ──────────────────────────────────────────────────────────────

def test_bulk_multiple_sets(engine: QueryEngine) -> None:
    engine.execute_bulk([
        'SET var_a = "alpha" WHERE host = "node2"',
        'SET var_b = "beta" WHERE host = "node2"',
    ])
    assert engine.execute('SELECT var_a FROM hostvars WHERE host = "node2"') == "alpha"
    assert engine.execute('SELECT var_b FROM hostvars WHERE host = "node2"') == "beta"


def test_bulk_same_file_second_sees_first(engine: QueryEngine, fresh_inv: Path) -> None:
    engine.execute_bulk([
        'SET bulk1 = "x" WHERE host = "node3"',
        'SET bulk2 = "y" WHERE host = "node3"',
    ])
    yaml = YAML()
    with (fresh_inv / "hostvars" / "node3" / "node3.yaml").open() as f:
        data = yaml.load(f)
    assert data["bulk1"] == "x"
    assert data["bulk2"] == "y"


def test_bulk_rollback_on_phase1_error(engine: QueryEngine, fresh_inv: Path) -> None:
    yaml_path = fresh_inv / "hostvars" / "node1" / "node1.yaml"
    original = yaml_path.read_text()
    with pytest.raises(QueryError):
        engine.execute_bulk([
            'SET rollback_var = "test" WHERE host = "node1"',
            'SET other = "x" WHERE host = "does_not_exist_xyz"',
        ])
    assert yaml_path.read_text() == original


def test_bulk_set_and_unset(engine: QueryEngine) -> None:
    engine.execute_bulk([
        'SET tmp_var = "temp" WHERE host = "node4"',
    ])
    engine.execute_bulk([
        'UNSET tmp_var FROM hostvars WHERE host = "node4"',
    ])
    hostvars = engine.execute('SELECT * FROM hostvars WHERE host = "node4"')
    assert "tmp_var" not in hostvars


def test_bulk_structural_command_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError):
        engine.execute_bulk(["CREATE HOST newhost"])


def test_bulk_select_raises(engine: QueryEngine) -> None:
    with pytest.raises(QueryError, match="SELECT"):
        engine.execute_bulk(['SELECT * FROM hostvars WHERE host = "node1"'])
