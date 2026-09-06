"""Gates de paridade entre a SPA principal e as rotas FastAPI."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from kairos_web.server import app

ROOT = Path(__file__).resolve().parent.parent


def _normalized_route(path: str) -> str:
    path = path.split("?", 1)[0]
    return re.sub(r"\{[^}]+\}", "{}", path)


def _spa_api_paths() -> set[str]:
    source = (ROOT / "kairos_web/ui/js/api.js").read_text(encoding="utf-8")
    paths = set(re.findall(r'request\("(/api/[^"?]+)', source))
    for template in re.findall(r"request\(`([^`]+)`", source):
        path = re.sub(
            r"\$\{([^}]+)\}",
            lambda match: "{}" if "encodeURIComponent" in match.group(1) else "",
            template,
        )
        paths.add(path)
    return {_normalized_route(path) for path in paths}


def test_cada_prefixo_de_api_da_spa_tem_rota_fastapi() -> None:
    def paths(routes) -> set[str]:
        found = {
            _normalized_route(route.path)
            for route in routes
            if getattr(route, "path", "").startswith("/api/")
        }
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                found.update(paths(included.routes))
        return found

    registered = paths(app.routes)

    assert _spa_api_paths() <= registered


def test_raiz_serve_spa_principal_que_registra_chat() -> None:
    response = TestClient(app).get("/")
    app_source = (ROOT / "kairos_web/ui/js/app.js").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "/ui/js/app.js" in response.text
    assert 'id: "chat"' in app_source
    assert 'id: "runtime"' in app_source
    assert 'href="/legacy"' not in response.text
