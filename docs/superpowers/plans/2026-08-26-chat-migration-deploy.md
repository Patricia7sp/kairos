# Chat Migration and Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provar paridade, remover caminhos legados, documentar operação e implantar a conclusão de Providers/Modelos/Chat no Komodo.

**Architecture:** A remoção do legado acontece somente depois de testes de paridade de Web e terminal. O deploy usa o `compose.yaml` versionado, preserva `kairos-data` e o segredo bind-mounted, e termina com verificações de saúde e cofre.

**Tech Stack:** pytest, Vitest/TypeScript, Docker Compose, GitHub Actions, Komodo, Tailscale.

**Spec:** `docs/superpowers/specs/2026-08-26-providers-chat-completion-design.md`

## Global Constraints

- Requer os três planos anteriores concluídos.
- Nenhum volume, sessão, credencial ou token pode ser apagado no deploy.
- Smoke tests externos só executam com credencial explicitamente configurada ou Ollama disponível.
- A interface legada só é removida após paridade automatizada.
- O serviço deve terminar `healthy` e acessível apenas pelo IP Tailscale configurado.

---

### Task 1: Parity gate for Web, CLI and providers

**Files:**
- Create: `tests/test_chat_parity.py`
- Create: `tests/test_provider_contract_matrix.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Verifies: identical selection, message persistence and event semantics across Web and CLI; every registered provider passes the adapter contract matrix.

- [ ] **Step 1: Write failing matrix tests**

```python
@pytest.mark.parametrize("provider", REGISTERED_PROVIDER_IDS)
async def test_adapter_contract_matrix(provider, fake_http):
    adapter = build_test_adapter(provider, fake_http)
    assert adapter.descriptor.id == provider
    assert await adapter.discover_models()
    assert [e.kind async for e in adapter.stream(simple_request())][-1] == "finish"


async def test_web_e_cli_persistem_o_mesmo_turno(tmp_path):
    web_state = await run_web_turn(tmp_path / "web")
    cli_state = await run_cli_turn(tmp_path / "cli")
    assert normalized_transcript(web_state) == normalized_transcript(cli_state)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_chat_parity.py tests/test_provider_contract_matrix.py`
Expected: FAIL for any remaining legacy divergence.

- [ ] **Step 3: Add CI gate using simulated transports**

Add the two files to the unit-test job; no secret environment variables and no network permission.

- [ ] **Step 4: Verify locally**

Run: `uv run pytest -q tests/test_chat_parity.py tests/test_provider_contract_matrix.py && git diff --check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_chat_parity.py tests/test_provider_contract_matrix.py .github/workflows/ci.yml
git commit -m "test(chat): adiciona gate de paridade e providers"
```

### Task 2: Remove runtime legacy paths

**Files:**
- Modify: `kairos_web/server.py:1060-1145`
- Delete: `kairos_providers/adapters/openai_adapter.py`
- Delete: `kairos_providers/adapters/anthropic_adapter.py`
- Delete: `kairos_providers/adapters/gemini_adapter.py`
- Delete: `kairos_providers/adapters/ollama_adapter.py`
- Modify: `kairos_providers/manager.py`
- Test: `tests/test_legacy_removal.py`

**Interfaces:**
- Produces: no `/legacy` mount and no direct legacy adapter implementation.

- [ ] **Step 1: Write failing absence tests**

```python
def test_legacy_route_nao_existe(client):
    assert client.get("/legacy/").status_code == 404


def test_server_nao_importa_provider_manager_direto():
    source = Path("kairos_web/server.py").read_text()
    assert "ProviderManager" not in source
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_legacy_removal.py`
Expected: FAIL while `/legacy` and direct imports exist.

- [ ] **Step 3: Remove mounts/imports and keep compatibility facade only if external tests require it**

The facade may convert old descriptors but cannot perform network calls or selection itself. Remove `web_dist` serving only after the SPA parity tests are green.

- [ ] **Step 4: Verify full Web/provider suites**

Run: `uv run pytest -q tests/test_legacy_removal.py tests/test_chat_parity.py tests/test_web.py tests/test_provider_contract_matrix.py && npm --prefix web test && npm --prefix web run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A kairos_web kairos_providers tests/test_legacy_removal.py
git commit -m "refactor(chat): remove caminhos legados apos paridade"
```

### Task 3: Packaging and operational documentation

**Files:**
- Modify: `pyproject.toml`
- Modify: `kairos.egg-info/SOURCES.txt`
- Modify: `README.md`
- Modify: `docs/decisoes.md`
- Create: `docs/providers-e-chat.md`
- Test: `tests/test_container.py`

**Interfaces:**
- Documents: provider setup, OpenRouter safe routing/free filter, Ollama, model switching, CLI, backup/rollback.

- [ ] **Step 1: Write failing image/package test**

```python
def test_imagem_contem_todos_os_modulos_de_interacao_e_adapters():
    expected = {"kairos_integration.interaction_service", "kairos_providers.adapters.openrouter"}
    assert expected <= importable_modules_in_image()
```

- [ ] **Step 2: Verify RED against current package manifest**

Run: `uv run pytest -q tests/test_container.py::DockerfileTests`
Expected: FAIL if any new package/file is omitted.

- [ ] **Step 3: Update manifest and write exact operator commands**

Document:

```bash
kairos auth vault-status --json
kairos chat --session minha-conversa "olá"
docker compose config --quiet
curl -fsS http://100.87.25.101:9119/api/health
```

Explain that OpenRouter free limits depend on the account and no real smoke runs automatically.

- [ ] **Step 4: Verify packaging and docs references**

Run: `uv run pytest -q tests/test_container.py tests/test_cli_surface.py && uv build && git diff --check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml kairos.egg-info/SOURCES.txt README.md docs/decisoes.md docs/providers-e-chat.md tests/test_container.py
git commit -m "docs(chat): documenta providers modelos e operacao"
```

### Task 4: Full verification and PR

**Files:**
- Modify only files required by failures proven in this task.

**Interfaces:**
- Produces: green branch and Pull Request against `main`.

- [ ] **Step 1: Run every local gate separately and capture exit codes**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q --deselect tests/test_container.py::RealImageTests
npm --prefix web test
npm --prefix web run typecheck
docker compose -f compose.yaml config --quiet
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 2: Build and run the real image integration suite**

Run: `docker build -t kairos:providers-chat . && KAIROS_TEST_IMAGE=kairos:providers-chat uv run pytest -q tests/test_container.py::RealImageTests`
Expected: PASS without external provider calls.

- [ ] **Step 3: Run security scans in CI-equivalent mode**

Run: `uv run pytest -q tests/test_security_audit.py tests/test_credential_vault.py tests/test_credential_migration.py`
Expected: PASS and no secret material in output.

- [ ] **Step 4: Push branch and open PR**

```bash
git push -u origin feat/providers-chat-completion
gh pr create --base main --head feat/providers-chat-completion \
  --title "feat: conclui providers modelos e chat" \
  --body $'## Resumo\n\n- moderniza todos os adapters e adiciona OpenRouter\n- unifica Web e CLI no InteractionService\n- entrega Chat, Modelos e Provedores na SPA principal\n\n## Validação\n\n- Python, frontend, imagem e segurança aprovados\n- APIs externas simuladas; nenhum crédito consumido'
gh pr checks --watch
```

The PR body lists each plan, local counts, image result, security result and states that real provider calls were not made.

- [ ] **Step 5: Merge only after every required check passes**

Run: `gh pr merge --squash --delete-branch`
Expected: PR state `MERGED` and `origin/main` contains the squash commit.

### Task 5: Sync Komodo stack and redeploy

**Files:**
- Runtime checkout: `/etc/komodo/stacks/kairos`
- Versioned config: `compose.yaml`

**Interfaces:**
- Produces: deployed `main`, persistent secret mount, healthy container.

- [ ] **Step 1: Record pre-deploy state without reading secrets**

```bash
docker inspect kairos --format '{{.Image}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}'
docker exec komodo-periphery-1 git -C /etc/komodo/stacks/kairos status --short --branch
docker exec komodo-periphery-1 stat -c '%a %u %g %s' /etc/komodo/secrets/kairos-vault-passphrase
```

Expected: container healthy; secret mode has no group/other bits and UID/GID are `10000:10000`.

- [ ] **Step 2: Fast-forward stack checkout**

Run: `docker exec komodo-periphery-1 git -C /etc/komodo/stacks/kairos pull --ff-only origin main`
Expected: checkout reaches the merged commit; `.env` remains untouched.

- [ ] **Step 3: Validate and deploy only the versioned compose**

```bash
docker exec komodo-periphery-1 docker compose -f /etc/komodo/stacks/kairos/compose.yaml config --quiet
docker exec komodo-periphery-1 docker compose -f /etc/komodo/stacks/kairos/compose.yaml up -d --build
```

Expected: `kairos` recreated; named volume is not removed.

- [ ] **Step 4: Wait on health condition and verify application boundaries**

```bash
docker inspect kairos --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
curl -fsS http://100.87.25.101:9119/api/health
docker exec -u 10000:10000 kairos /opt/kairos/.venv/bin/kairos auth vault-status --json
```

Expected: health `healthy`; API status `ok`; vault backend `encrypted` and state `unlocked`.

- [ ] **Step 5: Run no-cost production smoke**

```bash
docker exec kairos sh -lc 'token=$(cat /opt/data/web-token); curl -fsS -H "X-Kairos-Session-Token: $token" http://127.0.0.1:9119/api/models >/tmp/models.json; /opt/kairos/.venv/bin/python -c '\''import json; data=json.load(open("/tmp/models.json")); assert isinstance(data["models"], list); print(len(data["models"]))'\''; rm /tmp/models.json'
docker exec kairos sh -lc 'token=$(cat /opt/data/web-token); curl -fsS -H "X-Kairos-Session-Token: $token" http://127.0.0.1:9119/api/providers | /opt/kairos/.venv/bin/python -c '\''import json,sys; data=json.load(sys.stdin); assert "providers" in data; print(len(data["providers"]))'\'''
docker exec -u 10000:10000 kairos /opt/kairos/.venv/bin/python -c $'import asyncio,json\nfrom pathlib import Path\nimport websockets\nasync def check():\n token=Path("/opt/data/web-token").read_text().strip()\n async with websockets.connect("ws://127.0.0.1:9119/ws/chat?token="+token) as ws:\n  await ws.send(json.dumps({"type":"ping"}))\n  assert json.loads(await ws.recv())["type"] == "pong"\nasyncio.run(check())'
```

Do not submit a Chat message unless a provider credential is explicitly configured and the user authorizes a real call.

- [ ] **Step 6: Record deployment evidence**

Add the merged commit, image ID, start time, health result, provider counts and vault state to the PR or deployment record without secret identifiers.
