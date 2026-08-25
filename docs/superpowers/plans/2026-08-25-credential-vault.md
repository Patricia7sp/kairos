# Credential Vault Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remover segredos de provider do `auth.json`, armazená-los em keyring ou cofre criptografado e oferecer migração confirmada sem quebrar CLI, Web ou perfis.

**Architecture:** `kairos_security.credentials` define um contrato único e backends isolados. `SystemKeyringVault` usa o keyring do sistema quando disponível; `EncryptedFileVault` usa AES-256-GCM e uma chave derivada por Scrypt, permanece bloqueado após restart e nunca grava a chave junto do cofre. `CredentialService` escolhe o backend, expõe somente metadados e coordena uma migração em duas etapas do legado.

**Tech Stack:** Python 3.11+, dataclasses, `cryptography` (AESGCM/Scrypt), `keyring`, JSON atômico, unittest/pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-25-providers-modelos-chat-design.md`

## Global Constraints

- A ordem de armazenamento é keyring seguro, cofre criptografado e segredo externo.
- A chave de criptografia nunca é persistida ao lado do cofre.
- Estados públicos são `locked`, `unlocked`, `keyring`, `external` e `not_configured`.
- APIs retornam somente estado, método, origem, identificador e valor mascarado.
- Logs, exceções, `repr` e eventos não contêm segredo.
- A migração nunca remove plaintext antes de importar e verificar cada credencial.
- Remoção do plaintext exige confirmação explícita.
- O `AuthStore` legado continua legível durante a migração.
- Nenhum teste acessa keyring real ou API de provider.

---

### Task 1: Contrato e metadados públicos do cofre

**Files:**
- Create: `kairos_security/credentials/contracts.py`
- Create: `kairos_security/credentials/__init__.py`
- Test: `tests/test_credential_vault.py`

**Interfaces:**
- Produces: `VaultState`, `CredentialRef`, `CredentialMetadata`, `CredentialSecret`, `CredentialVault`, `VaultLockedError`, `CredentialNotFoundError`.

- [ ] **Step 1: Escrever testes das invariantes e redação**

```python
class CredentialContractTests(unittest.TestCase):
    def test_metadata_mascara_identificador_sem_guardar_segredo(self):
        meta = CredentialMetadata(
            ref=CredentialRef("openai", "primary"),
            auth_method="api_key",
            origin="vault",
            identifier="sk-producao-123456",
        )
        self.assertEqual(meta.masked_identifier, "sk-p…56")
        self.assertNotIn("sk-producao-123456", repr(meta))

    def test_secret_nao_expoe_valor_no_repr(self):
        secret = CredentialSecret({"api_key": "sk-nao-vazar"})
        self.assertNotIn("sk-nao-vazar", repr(secret))
        self.assertEqual(secret.reveal()["api_key"], "sk-nao-vazar")
```

- [ ] **Step 2: Rodar e confirmar RED**

Run: `uv run pytest tests/test_credential_vault.py::CredentialContractTests -q`
Expected: FAIL porque `kairos_security.credentials` não existe.

- [ ] **Step 3: Implementar os tipos congelados e o Protocol**

```python
class VaultState(StrEnum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    KEYRING = "keyring"
    EXTERNAL = "external"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class CredentialRef:
    provider: str
    credential_id: str


@dataclass(frozen=True)
class CredentialMetadata:
    ref: CredentialRef
    auth_method: str
    origin: str
    identifier: str = field(repr=False)

    @property
    def masked_identifier(self) -> str:
        value = self.identifier
        return "••••" if len(value) < 6 else f"{value[:4]}…{value[-2:]}"


class CredentialSecret:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def reveal(self) -> dict[str, str]:
        return dict(self._values)

    def __repr__(self) -> str:
        return "CredentialSecret(<redacted>)"
```

Definir `CredentialVault` como `Protocol` com `state`, `put`, `get`, `list` e `delete`. Validar provider/ID não vazios. Exceções carregam somente `provider/credential_id`.

- [ ] **Step 4: Rodar GREEN e lint**

Run: `uv run pytest tests/test_credential_vault.py::CredentialContractTests -q`
Expected: PASS.

Run: `uv run ruff check kairos_security/credentials tests/test_credential_vault.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_security/credentials tests/test_credential_vault.py
git commit -m "feat(security): define contrato do cofre de credenciais"
```

### Task 2: Cofre criptografado bloqueável

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `kairos_security/credentials/encrypted.py`
- Modify: `kairos_security/credentials/__init__.py`
- Test: `tests/test_credential_vault.py`

**Interfaces:**
- Consumes: contratos da Task 1.
- Produces: `EncryptedFileVault(path, scrypt_n=2**14)`, `.initialize(passphrase)`, `.unlock(passphrase)`, `.lock()`.

- [ ] **Step 1: Adicionar dependência criptográfica**

Run: `uv add 'cryptography>=46'`
Expected: `pyproject.toml` e `uv.lock` atualizados.

- [ ] **Step 2: Escrever testes de round-trip, bloqueio e senha incorreta**

```python
class EncryptedFileVaultTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self._tmp.name) / "credentials.vault"
        self.vault = EncryptedFileVault(self.path, scrypt_n=2**10)

    def test_roundtrip_criptografado_nao_contem_plaintext(self):
        self.vault.initialize("senha-mestra-forte")
        ref = CredentialRef("openai", "primary")
        self.vault.put(ref, CredentialSecret({"api_key": "sk-nao-vazar"}))
        self.assertEqual(self.vault.get(ref).reveal()["api_key"], "sk-nao-vazar")
        self.assertNotIn(b"sk-nao-vazar", self.path.read_bytes())

    def test_restart_volta_bloqueado(self):
        self.vault.initialize("senha-mestra-forte")
        fresh = EncryptedFileVault(self.path, scrypt_n=2**10)
        self.assertEqual(fresh.state, VaultState.LOCKED)
        with self.assertRaises(VaultLockedError):
            fresh.list("openai")

    def test_senha_incorreta_nao_altera_o_arquivo(self):
        self.vault.initialize("correta")
        before = self.path.read_bytes()
        with self.assertRaises(InvalidMasterPasswordError):
            EncryptedFileVault(self.path, scrypt_n=2**10).unlock("errada")
        self.assertEqual(self.path.read_bytes(), before)
```

- [ ] **Step 3: Rodar e confirmar RED**

Run: `uv run pytest tests/test_credential_vault.py::EncryptedFileVaultTests -q`
Expected: FAIL porque `EncryptedFileVault` não existe.

- [ ] **Step 4: Implementar formato versionado e escrita atômica**

```python
{
    "version": 1,
    "kdf": {"name": "scrypt", "salt": "<base64>", "n": 16384, "r": 8, "p": 1},
    "cipher": {"name": "aes-256-gcm", "nonce": "<base64>", "ciphertext": "<base64>"},
}
```

Derivar 32 bytes com `Scrypt`, criptografar o JSON interno completo com `AESGCM`, usar `b"kairos-credential-vault-v1"` como associated data e recriptografar com nonce aleatório novo a cada mutação. Gravar por `tmp + os.replace`, chmod `0600`, fsync do arquivo e diretório. Manter a chave somente em `bytearray` enquanto desbloqueado e sobrescrevê-la em `lock()`.

- [ ] **Step 5: Rodar GREEN, regressão e lint**

Run: `uv run pytest tests/test_credential_vault.py -q`
Expected: PASS.

Run: `uv run ruff check kairos_security/credentials tests/test_credential_vault.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock kairos_security/credentials tests/test_credential_vault.py
git commit -m "feat(security): adiciona cofre criptografado"
```

### Task 3: Backend de keyring e seleção segura

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `kairos_security/credentials/keyring_backend.py`
- Create: `kairos_security/credentials/service.py`
- Modify: `kairos_security/credentials/__init__.py`
- Test: `tests/test_credential_vault.py`

**Interfaces:**
- Consumes: `CredentialVault`, `EncryptedFileVault`.
- Produces: `KeyringClient` Protocol, `SystemKeyringVault`, `CredentialService`, `ExternalCredentialSource`.

- [ ] **Step 1: Adicionar keyring**

Run: `uv add 'keyring>=25'`
Expected: lock atualizado sem dependência de backend gráfico obrigatório.

- [ ] **Step 2: Escrever testes com fake realista**

```python
class KeyringVaultTests(unittest.TestCase):
    def test_keyring_disponivel_e_primeira_escolha(self):
        client = MemoryKeyring(priority=1)
        encrypted = LockedVault()
        service = CredentialService(keyring=SystemKeyringVault(client), encrypted=encrypted)
        service.put(CredentialRef("openai", "primary"), CredentialSecret({"api_key": "sk-x"}))
        self.assertEqual(service.state, VaultState.KEYRING)
        self.assertEqual(client.get_password("kairos/openai", "primary"), '{"api_key":"sk-x"}')

    def test_keyring_indisponivel_cai_no_cofre_sem_perder_estado_locked(self):
        service = CredentialService(
            keyring=SystemKeyringVault(FailingKeyring()), encrypted=LockedVault()
        )
        self.assertEqual(service.state, VaultState.LOCKED)
```

- [ ] **Step 3: Rodar RED**

Run: `uv run pytest tests/test_credential_vault.py::KeyringVaultTests -q`
Expected: FAIL de import.

- [ ] **Step 4: Implementar adapter e roteamento**

Serializar somente o payload secreto no keyring, com service `kairos/<provider>` e username `credential_id`. Guardar o índice não secreto em `credential-index.json` com escrita atômica. Considerar keyring utilizável somente quando backend possui `priority > 0` e uma sonda `set/get/delete` temporária completa sem exceção. `CredentialService` não alterna backend silenciosamente depois de iniciado.

- [ ] **Step 5: Rodar GREEN e lint**

Run: `uv run pytest tests/test_credential_vault.py -q`
Expected: PASS.

Run: `uv run ruff check kairos_security/credentials tests/test_credential_vault.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock kairos_security/credentials tests/test_credential_vault.py
git commit -m "feat(security): usa keyring com fallback criptografado"
```

### Task 4: Migração assistida do auth.json

**Files:**
- Create: `kairos_security/credentials/migration.py`
- Modify: `kairos_security/credentials/__init__.py`
- Test: `tests/test_credential_migration.py`

**Interfaces:**
- Consumes: `CredentialService`, formato `credential_pool` do `AuthStore`.
- Produces: `LegacyCredentialMigration.scan()`, `.import_and_verify()`, `.finalize(confirm=False)`, `MigrationReport`.

- [ ] **Step 1: Escrever testes das duas etapas e falha parcial**

```python
class LegacyCredentialMigrationTests(unittest.TestCase):
    def test_scan_nao_muda_arquivo_nem_cofre(self):
        before = self.auth_path.read_bytes()
        report = self.migration.scan()
        self.assertEqual(report.credentials_found, 2)
        self.assertEqual(self.auth_path.read_bytes(), before)
        self.assertEqual(self.vault.list(), [])

    def test_importa_verifica_e_so_remove_com_confirmacao(self):
        self.migration.import_and_verify()
        self.assertIn("sk-legado", self.auth_path.read_text())
        with self.assertRaises(MigrationConfirmationRequired):
            self.migration.finalize(confirm=False)
        self.migration.finalize(confirm=True)
        text = self.auth_path.read_text()
        self.assertNotIn("sk-legado", text)
        self.assertEqual(
            json.loads(text)["credential_pool"]["openai"][0]["credential_id"], "legacy-1"
        )

    def test_falha_de_verificacao_preserva_plaintext(self):
        vault = CorruptingVault()
        migration = LegacyCredentialMigration(self.auth_path, vault)
        with self.assertRaises(MigrationVerificationError):
            migration.import_and_verify()
        self.assertIn("sk-legado", self.auth_path.read_text())
```

- [ ] **Step 2: Rodar RED**

Run: `uv run pytest tests/test_credential_migration.py -q`
Expected: FAIL de import.

- [ ] **Step 3: Implementar migração idempotente**

Mapear cada entrada para ID estável `legacy-<índice+1>`, importar payload completo, reler e comparar antes de marcá-la como verificada. `finalize(confirm=True)` relê todas as credenciais do cofre e só então troca cada objeto secreto por `{credential_id, auth_method, masked_identifier}` via arquivo temporário e `os.replace`. Reexecução ignora referências já migradas e não duplica keyring.

- [ ] **Step 4: Rodar GREEN e auditoria de segredo**

Run: `uv run pytest tests/test_credential_migration.py tests/test_security_audit.py -q`
Expected: PASS.

Run: `uv run ruff check kairos_security/credentials tests/test_credential_migration.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_security/credentials tests/test_credential_migration.py
git commit -m "feat(security): migra credenciais legadas com confirmacao"
```

### Task 5: Integrar Web e AuthStore sem retornar segredos

**Files:**
- Modify: `kairos_cli/auth.py`
- Modify: `kairos_providers/manager.py`
- Modify: `kairos_web/server.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: `CredentialService`, `CredentialMetadata`, referências sanitizadas.
- Produces: `AuthStore(vault=...)`, `ProviderManager(secret_resolver=...)`, `/api/providers/save-key` seguro e `/api/providers/vault-status`.

- [ ] **Step 1: Escrever testes de integração**

```python
def test_save_key_nunca_grava_plaintext_no_auth_json(self):
    response = self.client.post(
        "/api/providers/save-key",
        json={"provider": "anthropic", "api_key": "sk-ant-nao-vazar"},
    )
    self.assertEqual(response.status_code, 200)
    self.assertNotIn("sk-ant-nao-vazar", self.auth_file.read_text())
    self.assertNotIn("api_key", response.json())


def test_provider_manager_resolve_referencia_pelo_cofre(self):
    manager = ProviderManager(
        auth_store={"openai": [{"credential_id": "primary"}]},
        secret_resolver=lambda provider, credential_id: {"api_key": "sk-resolvida"},
    )
    self.assertEqual(manager.get_provider("openai").api_key, "sk-resolvida")
```

- [ ] **Step 2: Rodar RED**

Run: `uv run pytest tests/test_cli.py tests/test_providers.py tests/test_web.py -q --maxfail=3`
Expected: os novos testes falham porque o caminho ainda grava plaintext.

- [ ] **Step 3: Adaptar consumidores**

`AuthStore.write_atomically()` rejeita objetos contendo `api_key`, `token`, `secret` ou `password` quando há vault configurado. `ProviderManager` aceita `secret_resolver(provider, credential_id)` e mantém variáveis externas com precedência. A rota salva no vault, testa a conexão usando o segredo apenas em memória e persiste somente a referência sanitizada. `vault-status` devolve estado e metadados mascarados.

- [ ] **Step 4: Rodar GREEN e regressão de API**

Run: `uv run pytest tests/test_cli.py tests/test_providers.py tests/test_web.py tests/test_security_audit.py -q`
Expected: PASS.

Run: `uv run ruff check kairos_cli kairos_providers kairos_web kairos_security tests/test_cli.py tests/test_providers.py tests/test_web.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kairos_cli/auth.py kairos_providers/manager.py kairos_web/server.py tests/test_cli.py tests/test_providers.py tests/test_web.py
git commit -m "feat(web): salva chaves de provider no cofre"
```

### Task 6: Comandos de unlock/migração e validação integral

**Files:**
- Modify: `kairos_cli/commands.py`
- Modify: `kairos_cli/handlers.py`
- Modify: `tests/test_cli_surface.py`
- Modify: `docs/superpowers/specs/2026-08-25-providers-modelos-chat-design.md`
- Modify: `kairos.egg-info/SOURCES.txt`

**Interfaces:**
- Consumes: `CredentialService`, `LegacyCredentialMigration`.
- Produces: `kairos auth vault-status`, `kairos auth vault-init`, `kairos auth vault-unlock`, `kairos auth migrate --confirm-remove-plaintext`.

- [ ] **Step 1: Escrever testes dos comandos sem segredo em stdout**

```python
def test_migrate_sem_confirmacao_apenas_relata(self):
    result = run_cli("auth", "migrate")
    self.assertEqual(result.code, 0)
    self.assertIn("confirmação necessária", result.stdout)
    self.assertNotIn("sk-legado", result.stdout)


def test_unlock_le_senha_por_getpass_e_nao_argumento(self):
    parser = build_parser()
    with self.assertRaises(SystemExit):
        parser.parse_args(["auth", "vault-unlock", "--password", "segredo"])
```

- [ ] **Step 2: Rodar RED**

Run: `uv run pytest tests/test_cli_surface.py -q`
Expected: FAIL porque os subcomandos não existem.

- [ ] **Step 3: Implementar comandos seguros**

Senha-mestra entra somente por `getpass.getpass()` em TTY; aceitar `KAIROS_VAULT_PASSPHRASE_FILE` como fonte administrada, nunca valor em argumento ou variável direta. JSON de saída contém estado, contagens e IDs mascarados. O comando de remoção exige a flag literal `--confirm-remove-plaintext`.

- [ ] **Step 4: Atualizar documentação e manifesto**

Marcar fase 2 como implementada, documentar estados, restart bloqueado e procedimento de migração. Adicionar os novos módulos/testes ao `SOURCES.txt`.

- [ ] **Step 5: Rodar verificação completa**

Run: `timeout 150s uv run pytest -q --maxfail=2`
Expected: todos os testes passam.

Run: `uv run ruff check .`
Expected: PASS.

Run: `uv run ruff format --check .`
Expected: PASS.

Run: `git diff --check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kairos_cli/commands.py kairos_cli/handlers.py tests/test_cli_surface.py docs/superpowers/specs/2026-08-25-providers-modelos-chat-design.md kairos.egg-info/SOURCES.txt
git commit -m "docs: registra cofre de credenciais implementado"
```
