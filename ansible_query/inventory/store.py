import copy
import os
from pathlib import Path
from typing import Any

from ansible_vault import Vault
from ruamel.yaml import YAML


class Store:
    """File I/O layer: loads/writes YAML and vault files with cache and atomic writes."""

    def __init__(self, inventory_path: Path) -> None:
        self._path = inventory_path
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._cache: dict[Path, Any] = {}
        self._vault: Vault | None = None

    # ── public read API ───────────────────────────────────────────────────────

    def load_hostvars(self, hostname: str) -> dict[str, Any]:
        yaml_path = self._path / "hostvars" / hostname / f"{hostname}.yaml"
        vault_path = self._path / "hostvars" / hostname / f"{hostname}.vault"
        return self._load_merged(yaml_path, vault_path)

    def load_groupvars(self, group: str) -> dict[str, Any]:
        yaml_path = self._path / "groupvars" / group / f"{group}.yaml"
        vault_path = self._path / "groupvars" / group / f"{group}.vault"
        return self._load_merged(yaml_path, vault_path)

    def load_yaml_raw(self, path: Path) -> Any:
        """Return a deep copy of the cached CommentedMap, safe for in-place modification."""
        return copy.deepcopy(self._load_yaml_cached(path))

    # ── public write API ──────────────────────────────────────────────────────

    def write_yaml(self, path: Path, data: Any) -> None:
        """Write YAML atomically (.tmp → rename). Preserves comments if data is a CommentedMap."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        try:
            with tmp.open("w") as f:
                self._yaml.dump(data, f)
            tmp.rename(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        self.invalidate(path)

    def write_vault(self, path: Path, data: dict[str, Any]) -> None:
        """Encrypt data and write vault file atomically (.tmp → rename)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        vault = self._get_vault()
        tmp = path.parent / (path.name + ".tmp")
        try:
            with tmp.open("w") as f:
                vault.dump(data, f)
            tmp.rename(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        self.invalidate(path)

    def load_vault_raw(self, path: Path) -> dict[str, Any]:
        """Return a mutable copy of vault data, suitable for in-place modification."""
        return dict(self._load_vault_cached(path))

    def flush_bulk(self, pending: dict[Path, dict[str, Any]]) -> None:
        """Atomically write all staged files: write all .tmp first, then rename all."""
        tmp_files: list[tuple[Path, Path]] = []
        try:
            for path, data in pending.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.parent / (path.name + ".tmp")
                if path.suffix == ".vault":
                    vault = self._get_vault()
                    with tmp.open("w") as f:
                        vault.dump(data, f)
                else:
                    with tmp.open("w") as f:
                        self._yaml.dump(data, f)
                tmp_files.append((tmp, path))
            for tmp, path in tmp_files:
                tmp.rename(path)
                self.invalidate(path)
        except Exception:
            for tmp, _ in tmp_files:
                tmp.unlink(missing_ok=True)
            raise

    def invalidate(self, path: Path) -> None:
        self._cache.pop(path, None)

    # ── internal ──────────────────────────────────────────────────────────────

    def _load_merged(self, yaml_path: Path, vault_path: Path) -> dict[str, Any]:
        yaml_vars = dict(self._load_yaml_cached(yaml_path))
        vault_vars = dict(self._load_vault_cached(vault_path))
        overlap = set(yaml_vars) & set(vault_vars)
        if overlap:
            raise ValueError(
                f"Variables present in both .yaml and .vault: {sorted(overlap)!r}"
            )
        return {**yaml_vars, **vault_vars}

    def _load_yaml_cached(self, path: Path) -> Any:
        if path in self._cache:
            return self._cache[path]
        if not path.exists():
            return {}
        with path.open() as f:
            data = self._yaml.load(f)
        data = data if data is not None else {}
        self._cache[path] = data
        return data

    def _load_vault_cached(self, path: Path) -> dict[str, Any]:
        if path in self._cache:
            return self._cache[path]
        if not path.exists():
            return {}
        vault = self._get_vault()
        data: dict[str, Any] = vault.load(path.read_text()) or {}
        for key in data:
            if not str(key).startswith("vault_"):
                raise ValueError(
                    f"{path}: vault variable {key!r} must have 'vault_' prefix"
                )
        self._cache[path] = data
        return data

    def _get_vault(self) -> Vault:
        if self._vault is None:
            pw_file = os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE")
            if not pw_file:
                raise RuntimeError(
                    "ANSIBLE_VAULT_PASSWORD_FILE environment variable is not set"
                )
            password = Path(pw_file).read_text().strip()
            self._vault = Vault(password)
        return self._vault
