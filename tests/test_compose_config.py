import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_configura_fallback_criptografado_com_segredo_somente_leitura():
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "config"],
        check=True,
        capture_output=True,
        text=True,
    )
    service = yaml.safe_load(result.stdout)["services"]["kairos"]

    assert service["environment"]["KAIROS_DISABLE_KEYRING"] == "1"
    assert service["environment"]["KAIROS_VAULT_PASSPHRASE_FILE"] == (
        "/run/secrets/kairos-vault-passphrase"  # noqa: S105
    )
    assert {
        "type": "bind",
        "source": "/etc/komodo/secrets/kairos-vault-passphrase",
        "target": "/run/secrets/kairos-vault-passphrase",
        "read_only": True,
        "bind": {},
    } in service["volumes"]
