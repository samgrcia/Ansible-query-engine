from pathlib import Path

import pytest
from ansible_vault import Vault

from ansible_query.inventory.store import Store

# ── plain YAML loading ────────────────────────────────────────────────────────

def test_load_hostvars_yaml(
    inventory_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    store = Store(inventory_path)
    vars = store.load_hostvars("node1")
    assert vars["ansible_host"] == "10.0.0.1"
    assert vars["env"] == "production"


def test_load_groupvars_yaml(
    inventory_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    store = Store(inventory_path)
    vars = store.load_groupvars("webservers")
    assert vars["http_port"] == 80
    assert vars["app_role"] == "web"


def test_load_nonexistent_host_returns_empty(inventory_path: Path) -> None:
    store = Store(inventory_path)
    assert store.load_hostvars("ghost_host") == {}


# ── vault loading ─────────────────────────────────────────────────────────────

def test_load_hostvars_includes_vault(
    inventory_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    store = Store(inventory_path)
    vars = store.load_hostvars("node1")
    assert vars["vault_root_password"] == "secret123"
    assert vars["ansible_host"] == "10.0.0.1"


def test_load_groupvars_includes_vault(
    inventory_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    store = Store(inventory_path)
    vars = store.load_groupvars("webservers")
    assert vars["vault_ssl_cert"] == "my_cert_content"
    assert vars["http_port"] == 80


def test_vault_missing_env_raises(
    inventory_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANSIBLE_VAULT_PASSWORD_FILE", raising=False)
    store = Store(inventory_path)
    with pytest.raises(RuntimeError, match="ANSIBLE_VAULT_PASSWORD_FILE"):
        store.load_hostvars("node1")


def test_vault_missing_prefix_raises(
    tmp_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    host_dir = tmp_path / "hostvars" / "badhost"
    host_dir.mkdir(parents=True)
    vault = Vault(vault_password_file.read_text().strip())
    with (host_dir / "badhost.vault").open("w") as f:
        vault.dump({"no_prefix": "oops"}, f)
    store = Store(tmp_path)
    with pytest.raises(ValueError, match="vault_"):
        store.load_hostvars("badhost")


def test_yaml_vault_duplicate_raises(
    tmp_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    host_dir = tmp_path / "hostvars" / "duphost"
    host_dir.mkdir(parents=True)
    (host_dir / "duphost.yaml").write_text("vault_shared: from_yaml\n")
    vault = Vault(vault_password_file.read_text().strip())
    with (host_dir / "duphost.vault").open("w") as f:
        vault.dump({"vault_shared": "from_vault"}, f)
    store = Store(tmp_path)
    with pytest.raises(ValueError, match=r"\.yaml and \.vault"):
        store.load_hostvars("duphost")


# ── write + atomic rename ─────────────────────────────────────────────────────

def test_write_yaml_creates_file(tmp_path: Path) -> None:
    store = Store(tmp_path)
    target = tmp_path / "hostvars" / "node9" / "node9.yaml"
    store.write_yaml(target, {"env": "staging"})
    assert target.exists()
    assert not (target.parent / "node9.yaml.tmp").exists()


def test_write_yaml_content_readable(tmp_path: Path) -> None:
    store = Store(tmp_path)
    target = tmp_path / "test.yaml"
    store.write_yaml(target, {"key": "value", "num": 42})
    fresh = Store(tmp_path)
    data = fresh.load_yaml_raw(target)
    assert data["key"] == "value"
    assert data["num"] == 42


def test_write_yaml_preserves_comments(tmp_path: Path) -> None:
    source = tmp_path / "vars.yaml"
    source.write_text("# important comment\nenv: development\n")
    store = Store(tmp_path)
    cm = store.load_yaml_raw(source)
    cm["env"] = "production"
    store.write_yaml(source, cm)
    content = source.read_text()
    assert "# important comment" in content
    assert "production" in content


def test_write_yaml_invalidates_cache(tmp_path: Path) -> None:
    store = Store(tmp_path)
    target = tmp_path / "test.yaml"
    store.write_yaml(target, {"v": 1})
    _ = store.load_yaml_raw(target)          # populate cache
    store.write_yaml(target, {"v": 2})
    assert store.load_yaml_raw(target)["v"] == 2


def test_write_vault_roundtrip(
    tmp_path: Path,
    vault_password_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", str(vault_password_file))
    store = Store(tmp_path)
    target = tmp_path / "test.vault"
    store.write_vault(target, {"vault_secret": "mysecret"})
    assert target.exists()
    assert not (tmp_path / "test.vault.tmp").exists()
    vault = Vault(vault_password_file.read_text().strip())
    assert vault.load(target.read_text())["vault_secret"] == "mysecret"


def test_cache_invalidation(tmp_path: Path) -> None:
    store = Store(tmp_path)
    target = tmp_path / "test.yaml"
    target.write_text("v: 1\n")
    assert store.load_yaml_raw(target)["v"] == 1
    target.write_text("v: 99\n")
    assert store.load_yaml_raw(target)["v"] == 1  # stale cache
    store.invalidate(target)
    assert store.load_yaml_raw(target)["v"] == 99
