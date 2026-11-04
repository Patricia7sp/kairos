# Build de imagem com dependências do lock

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execução direta; revisão independente ao final. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que a imagem Python resolva versões diferentes de uv.lock durante o build.

**Architecture:** Copiar uv.lock no contexto, usar uv 0.12.5 oficial fixado por digest como ferramenta temporária do BuildKit e sincronizar a venv existente com --locked, sem dependências de desenvolvimento. Preservar layout, instalação editável, shims, UID e s6. Não alterar a produção nesta implementação.

**Tech Stack:** Docker/BuildKit, Python 3.11, uv, unittest/pytest.

**Spec:** AGENTS.md (ambiente, imagem, CI e entrega); diagnóstico E0 no PR #132: fontes c6a083f idênticos, mas 16 versões instaladas divergem do lock em app e broker.

## Global Constraints

- Python 3.11; nenhuma mudança de dependência de produto ou uv.lock para acomodar o defeito.
- Imagem uv oficial: ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1.
- Não desativar políticas, testes, lint ou controles do container; não incluir ferramentas de teste no runtime.
- Testes verificam a imagem executada e sua relação com o lock, sem ler código Python/Dockerfile.
- CI integral local e remoto antes de merge; deploy permanece separado até autorização aplicável.

## Review Focus

- Lock fora do contexto: .dockerignore deve permitir seu COPY; build real precisa exercitar isso.
- Arquivo desatualizado: --locked deve recusar divergência entre projeto e lock, não atualizar silenciosamente.
- Ambiente runtime: versões instaladas presentes no lock; requisitos diretos presentes; extras dev ausentes.
- Layout/entrypoints editáveis: preservar /opt/kairos/.venv e /opt/kairos/bin, com regressões reais de s6/UID.
- Ferramenta uv: digest fixo e montagem temporária; não adicionar seu binário permanente à imagem.

## Task 1: demonstrar e corrigir o consumo do lock

**Files:** Modify Dockerfile, .dockerignore; Test tests/test_container.py::RealImageTests.

**Interfaces:** Consome pyproject.toml/uv.lock; produz imagem com venv /opt/kairos/.venv e dependências runtime alinhadas ao lock.

- [x] Acrescentar teste real que extrai name/version de importlib.metadata no container e compara com uv.lock, com normalização PEP 503; exigir os requisitos diretos do pyproject e rejeitar extras dev.
- [x] Executar o novo teste contra o ID imutável da imagem atualmente implantada; esperar falhas por versões diferentes, não erro de setup ou skip.
- [x] Permitir uv.lock no contexto; copiar junto de pyproject/README; substituir pip -e por uv sync --locked --no-dev --no-cache --python /usr/local/bin/python3.11 --no-managed-python, com uv temporário de estágio fixado por digest.
- [ ] Construir aplicação e broker isolados; executar teste novo e RealImageTests contra as imagens novas; validar também recusa de lock inconsistente em contexto sintético.
- [ ] Executar scripts/ci.sh integral com Codex 0.154.0 verificado, frontend deps presentes, revisão independente e CI remoto.
- [ ] Commit em português e PR separado; não modificar o PR #132 com código nem declarar E0 aprovado por essa correção isolada.

## Evidência parcial desta execução

Baseline DockerfileTests: 11 passed; a primeira tentativa sem permissões do daemon falhou ambientalmente e foi repetida com acesso correto. RED na imagem implantada: divergências reais (16 versões e 2 distribuições adicionais). GREEN em app e broker isolados. Projeto sintético com versão alterada e lock intacto foi recusado por --locked; uv ausente do runtime final.

A revisão independente apontou que a checagem inicial de dev só cobria dependências diretas. Uma imagem mutante com pluggy mostrou o falso negativo; a asserção reforçada percorre as arestas default do lock e a rejeita. A imagem correta voltou a passar. O grafo usa união dos ramos de plataforma para não depender do SO do host que chama Docker; não é um novo resolvedor de markers.

CI integral local em execução; ainda sem aprovação do conjunto. npm ci preservou os locks e reportou os avisos existentes de ferramentas dev (7 vulnerabilidades por frontend: 3 moderadas, 2 altas, 2 críticas); upgrades/audit fix ficam fora desta correção de consumo do lock. Não afirmar segurança global ou reprodução bit a bit da imagem: base/apt e build backend continuam com os limites anteriores. Produção não alterada.
