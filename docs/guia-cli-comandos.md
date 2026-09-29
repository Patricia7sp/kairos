# Como adicionar um comando à CLI do Kairos

A CLI do Kairos é a terceira camada de entrada — **Web ─ CLI ─ API → Core**. Ela
não reimplementa o agente: os comandos consomem as mesmas units que a Web usa
(`kairos_cli/chat.py` e `kairos_web/server.py` importam o mesmo
`build_interaction_router`; decisão D-PC.1). Um comando novo só **atravessa** a
árvore até uma unit existente; se a unit não existe, o esforço é na unit, não
na CLI.

## O contrato

- **Sem efeito real é pior que ausência.** Um comando que "reporta sucesso"
  sem fazer nada é bug. Comando pendente fica `NOT_IMPLEMENTED` e sai com 69 +
  mensagem do que falta (decisão D-CLI.9) — nunca com 0.
- **Comportamento real, não snapshot.** Testes afirmam contratos (ler estado
  persistido de verdade), não contagens congeladas.
- **Nada de lógica core na CLI.** Se o comando começou a duplicar o core,
  ele está no lugar errado: a lógica vai para a unit, o handler só a chama.

## Passo a passo

1. **Declare na árvore** `kairos_cli/commands.py` — um `_c(...)`/`_sub(...)`
   com `help`, `status=Status.IMPLEMENTED` e `unit=` (a unit que sustenta o
   comando). A árvore é declarada como dados de propósito: a superfície inteira
   fica legível de uma vez e a contagem deixa de ser folclore.
2. **Se o comando é do Kairos** (não existe no legado): adicione o nome ao
   conjunto `PROPRIOS_DO_KAIROS` em `tests/test_cli_surface.py` — o teste de
   evidência conta 48 herdados do legado e os próprios à parte; apagar isso
   quebra a contagem como evidência.
3. **Argumentos específicos** em `kairos_cli/main.py` `_extra_args(...)` —
   posicionais e flags que só esse comando tem (há um dispatcher por
   `command`/`subcommand`; existe um toll de complexidade deliberado ali).
4. **Handler** em `kairos_cli/handlers.py`: função `cmd_<comando>(args)` que
   chama a unit (lazy import — o projeto corta ciclo entre camadas assim) e
   devolve `ExitCode` (`OK=0`, `ERROR=1`, `USAGE=2`, `NOT_IMPLEMENTED=69`).
   Registre em `HANDLERS`. Sem handler, `main` intercepta e recusa com 69.
5. **Testes.** A superfície e o comportamento:
   - `tests/test_cli_surface.py` valida ajuda, handler e contagens —
     rodo sempre que mudo a árvore.
   - Teste de comportamento real, no padrão dos existentes (ex.:
     `tests/test_cli_insights.py` lê `state.db` de verdade via
     `connect`+`initialize_schema`; `tests/test_cli_shell.py` injeta o
     `evaluate` do shell). Sem change-detector.
6. **Onde mora a interatividade.** Se o comando é o acolhimento do terminal,
   ele entra no menu do shell em `kairos_cli/shell.py` (`_menu`) — uma opção
   por linha, delegando ao `main` com o argv exato que o usuário digitaria.

## Validação obrigatória antes do merge

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
scripts/ci.sh --fast   # mesmo pipeline dos jobs do CI
```

O `test_superficie_totalmente_implementada_sem_pendencias` exige que todo
comando declarado esteja `IMPLEMENTED` com handler — não há "ado que existe na
árvore e não faz nada" aceitável.