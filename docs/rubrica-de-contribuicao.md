# Rubrica de contribuição

As regras de `domain.md` §2.4, §2.6 e §2.7 que regem **como o projeto é
desenvolvido**, não como o software se comporta. Estão aqui, e não em código,
porque nenhuma execução as exercitaria — encená-las como função Python daria
um teste que só prova que uma constante existe.

Elas estão registradas em `kairos_domain/rules.py` com `kind=PROCESS`, para
que a contagem dos 7 grupos permaneça completa e auditável.

## Segredos

- `.env` é **só** para segredos: chaves, tokens, senhas. Todo ajuste
  comportamental vai em `config.yaml`.
- Rejeitar PRs que digam ao usuário *"set X in your .env"* a menos que `X`
  seja credencial. Misturar config e segredo no mesmo arquivo faz o arquivo
  inteiro herdar o tratamento de segredo: não versionável, não documentável,
  não diffável em review.

## Superfície

- Descrições de schema **não podem** mencionar ferramentas de outro toolset
  pelo nome — o modelo alucinaria chamadas para ferramentas que não existem
  no contexto dele.
- Menus interativos de CLI **devem** usar curses.

## O que se rejeita

| Rejeitado | Razão declarada |
|---|---|
| **Infraestrutura especulativa** | *"Hooks, callbacks, or extension points with no concrete consumer. Adding a hook is easy; removing one after plugins depend on it is hard."* Exceção: **não** é especulativo se um contribuidor tem caso de uso real declarado, mesmo que o consumidor seja entregue à parte |
| **Novas env vars para config não-secreta** | `.env` é só para credencial |
| **Nova ferramenta core** quando terminal + file já resolvem, ou quando uma skill resolveria | É a Lei 2. *"If the only barrier is file visibility on a remote backend, fix the mount, not the toolset"* |
| **Testes detectores de mudança** | Congelar catálogos de modelo, literais de versão de config ou contagens de enumeração. Um teste assim falha quando o sistema evolui corretamente |

> **Nota de ironia produtiva.** A última linha condena "congelar contagens de
> enumeração" — e foi exatamente uma contagem congelada (`len(Platform) == 23`)
> que quebrou na Tarefa 02 e revelou que a spec contava errado. A lição não é
> que a regra esteja errada: é que a asserção certa é sobre a **propriedade**
> (plataformas de plugin resolvem dinamicamente), não sobre o **número**.
