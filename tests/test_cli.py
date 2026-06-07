import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from ansible_query.cli import cli


@pytest.fixture
def fresh_inv(tmp_path: Path, inventory_path: Path) -> Path:
    inv = tmp_path / "inventory"
    shutil.copytree(inventory_path, inv)
    return inv


def invoke(args: list[str], fresh_inv: Path, vault_password_file: Path):
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["--inventory", str(fresh_inv)] + args,
        env={"ANSIBLE_VAULT_PASSWORD_FILE": str(vault_password_file)},
    )


# ── SELECT output formats ─────────────────────────────────────────────────────

def test_cli_select_json_scalar(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(['SELECT env FROM hostvars WHERE host = "node1"'], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert json.loads(r.output) == "production"


def test_cli_select_json_dict(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(['SELECT * FROM groupvars WHERE group = "webservers"'], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["http_port"] == 80
    assert data["vault_ssl_cert"] == "my_cert_content"


def test_cli_select_yaml_output(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(
        ['SELECT env FROM hostvars WHERE host = "node1"', "--output", "yaml"],
        fresh_inv, vault_password_file,
    )
    assert r.exit_code == 0
    assert "production" in r.output


def test_cli_select_table_scalar(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(
        ['SELECT env FROM hostvars WHERE host = "node1"', "--output", "table"],
        fresh_inv, vault_password_file,
    )
    assert r.exit_code == 0
    assert "production" in r.output


def test_cli_select_table_multihost(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(
        ['SELECT env FROM hostvars WHERE host = "node*"', "--output", "table"],
        fresh_inv, vault_password_file,
    )
    assert r.exit_code == 0
    assert "node1" in r.output
    assert "production" in r.output


def test_cli_select_table_flat_dict(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(
        ['SELECT * FROM groupvars WHERE group = "webservers"', "--output", "table"],
        fresh_inv, vault_password_file,
    )
    assert r.exit_code == 0
    assert "http_port" in r.output
    assert "80" in r.output


# ── mutating commands ─────────────────────────────────────────────────────────

def test_cli_set_prints_done(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(['SET env = "staging" WHERE host = "node1"'], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "Done" in r.output


def test_cli_set_persists_on_disk(fresh_inv: Path, vault_password_file: Path) -> None:
    invoke(['SET env = "staging" WHERE host = "node1"'], fresh_inv, vault_password_file)
    r = invoke(['SELECT env FROM hostvars WHERE host = "node1"'], fresh_inv, vault_password_file)
    assert json.loads(r.output) == "staging"


def test_cli_unset_prints_done(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(['UNSET env FROM hostvars WHERE host = "node1"'], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "Done" in r.output


def test_cli_create_host_prints_done(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(["CREATE HOST newhost IN GROUPS webservers"], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "Done" in r.output


def test_cli_drop_host_prints_done(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(["DROP HOST standalone"], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "Done" in r.output


# ── dry-run ───────────────────────────────────────────────────────────────────

def test_cli_dry_run_does_not_write(fresh_inv: Path, vault_password_file: Path) -> None:
    yaml_path = fresh_inv / "hostvars" / "node1" / "node1.yaml"
    original = yaml_path.read_text()
    r = invoke(['SET env = "dry" WHERE host = "node1"', "--dry-run"], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "Dry run" in r.output
    assert yaml_path.read_text() == original


def test_cli_dry_run_shows_matched_hosts(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(['SET env = "dry" WHERE host = "node*"', "--dry-run"], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "node1" in r.output
    assert "node2" in r.output


def test_cli_dry_run_create_does_not_write(fresh_inv: Path, vault_password_file: Path) -> None:
    nodes_path = fresh_inv / "nodes.yaml"
    original = nodes_path.read_text()
    r = invoke(["CREATE HOST newhost IN GROUPS webservers", "--dry-run"], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "newhost" in r.output
    assert nodes_path.read_text() == original


def test_cli_dry_run_drop_shows_remove(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(["DROP HOST standalone", "--dry-run"], fresh_inv, vault_password_file)
    assert r.exit_code == 0
    assert "standalone" in r.output
    assert (fresh_inv / "hostvars" / "standalone").exists()  # not actually deleted


def test_cli_dry_run_select_executes_normally(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(
        ['SELECT env FROM hostvars WHERE host = "node1"', "--dry-run"],
        fresh_inv, vault_password_file,
    )
    assert r.exit_code == 0
    assert json.loads(r.output) == "production"


# ── error handling ────────────────────────────────────────────────────────────

def test_cli_missing_inventory() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--inventory", "/nonexistent/xyz", 'SELECT * FROM hostvars WHERE host = "x"'])
    assert r.exit_code != 0


def test_cli_parse_error(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(["INVALID QUERY SYNTAX"], fresh_inv, vault_password_file)
    assert r.exit_code != 0


def test_cli_query_error_exits_nonzero(fresh_inv: Path, vault_password_file: Path) -> None:
    r = invoke(['SET env = "x" WHERE host = "ghost_xyz"'], fresh_inv, vault_password_file)
    assert r.exit_code != 0
