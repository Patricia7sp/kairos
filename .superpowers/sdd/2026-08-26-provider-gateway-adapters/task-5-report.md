# Task 5 — Gemini e Ollama nativos

## Entrega

- Adicionado `GeminiNativeAdapter` para `streamGenerateContent` em SSE, com
  descoberta por `/models`, autenticação por `x-goog-api-key` ou Bearer OAuth,
  conversão de `functionCall`/`functionResponse` e uso de tokens.
- Adicionado `OllamaNativeAdapter` para `/api/tags` e `/api/chat` NDJSON, sem
  qualquer credencial. Indisponibilidade do daemon retorna
  `ConnectionStatus.state == "unavailable"`.
- Streams só confirmam tool calls, uso e término após o evento terminal;
  truncamento vira erro de rede normalizado. `CancelledError` não é capturado.
- Chaves Gemini permanecem nos headers em memória, nunca na URL. Reprs e erros
  normalizados não retêm payloads externos, chaves ou tokens.
- `ConnectionStatus` ganhou o campo aditivo `state`, com padrão `available`.

## Cobertura adicionada

- Payload e eventos Gemini, API key/OAuth, descoberta, EOF truncado, ausência
  de credencial e redaction de erro.
- Payload e eventos Ollama, descoberta de tags, daemon ausente, EOF truncado e
  mapeamento de modelo inexistente.

## Verificação

- `uv run pytest -q tests/test_gemini_native_adapter.py tests/test_ollama_native_adapter.py`
- `uv run pytest -q tests/test_provider_adapter_contract.py tests/test_openai_responses_adapter.py tests/test_anthropic_messages_adapter.py tests/test_providers.py tests/test_gemini_native_adapter.py tests/test_ollama_native_adapter.py`
- `uv run pytest -q -x` — 912 passed, 19 skipped, 5539 subtests passed.
- `uv run ruff check kairos_providers tests`
- `git diff --check`
