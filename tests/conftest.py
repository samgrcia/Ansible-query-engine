import shutil
from pathlib import Path

import pytest
from ansible_vault import Vault

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "inventory"
VAULT_PASSWORD = "testpassword"


@pytest.fixture(scope="session")
def vault_password_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pwfile = tmp_path_factory.mktemp("vault_pw") / "password"
    pwfile.write_text(VAULT_PASSWORD)
    return pwfile


@pytest.fixture(scope="session")
def inventory_path(tmp_path_factory: pytest.TempPathFactory, vault_password_file: Path) -> Path:
    inv_dir = tmp_path_factory.mktemp("inventory")
    shutil.copytree(FIXTURES_DIR, inv_dir, dirs_exist_ok=True)

    vault = Vault(VAULT_PASSWORD)

    vault_files: dict[Path, dict] = {
        inv_dir / "hostvars" / "node1" / "node1.vault": {
            "vault_root_password": "secret123",
        },
        inv_dir / "groupvars" / "webservers" / "webservers.vault": {
            "vault_ssl_cert": "my_cert_content",
        },
    }
    for path, data in vault_files.items():
        with path.open("w") as f:
            vault.dump(data, f)

    return inv_dir
