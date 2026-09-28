"""Receita de verificação pós-edição de código — detecção e manifesto.

Origem no legado: `agent/verify/{recipes,environment}.py`, consumidos por
`hermes_cli/verify_cmd.py` (port scoped de superagent-ai/grok-cli
`src/verify/recipes.ts` e `environment.ts`). Referência de spec:
`_reversa_sdd/hermes-cli/` — o `verify` é a verificação pós-edição de código.

**Layer ownership.** Este módulo possui a receita de runtime profunda:
framework, bootstrap/build/test e o start + porta + path de readiness. A
camada de *project facts* do legado (`detect_project_facts`) não foi portada
para o Kairos (D-CLI.10), então não há mescla de comandos de outra camada a
reconciliar.

**Divergência deliberada (D-CLI.10).** O manifesto fica em
`.kairos/environment.json`, não em `.hermes/environment.json`: o namespace do
próprio projeto e a única lugar onde um reparo/alteração de receita é decisão
do usuário no checkout que está verificando.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "MANIFEST_VERSION",
    "Recipe",
    "detect_package_manager",
    "detect_recipe",
    "load_manifest",
    "load_or_detect",
    "manifest_path",
    "save_manifest",
]

#: Versão do manifesto `.kairos/environment.json`. Coincide com a v1 do
#: legado: o formato é o mesmo, só mudou o diretório.
MANIFEST_VERSION = 1
_MANIFEST_RELPATH = Path(".kairos") / "environment.json"


@dataclass
class Recipe:
    """Receita executável de verificação de um projeto.

    Espelha o `VerifyRecipe` do grok-cli com um conjunto reduzido de campos:
    ``name`` é o rótulo humano, ``kind`` o id do detector, e as listas de
    comando são strings shell executadas na raiz do projeto.
    """

    name: str
    kind: str = "unknown"
    bootstrap: list[str] = field(default_factory=list)
    build: list[str] = field(default_factory=list)
    test: list[str] = field(default_factory=list)
    start: str | None = None
    port: int | None = None
    readiness_path: str = "/"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "bootstrap": list(self.bootstrap),
            "build": list(self.build),
            "test": list(self.test),
            "start": self.start,
            "port": self.port,
            "readinessPath": self.readiness_path,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Recipe | None:
        """Carregador tolerante espelhando ``normalizeVerifyRecipe``.

        Qualquer problema de forma devolve ``None`` em vez de levantar — um
        manifesto corrompido degrada para detecção nova, não quebra o
        `verify`.
        """
        if not isinstance(raw, dict):
            return None
        name = raw.get("name") or raw.get("appLabel")
        if not isinstance(name, str) or not name.strip():
            return None
        kind = raw.get("kind") or raw.get("appKind") or "unknown"
        if not isinstance(kind, str) or not kind.strip():
            kind = "unknown"

        def as_strings(value: Any) -> list[str]:
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            if not isinstance(value, list):
                return []
            return [v.strip() for v in value if isinstance(v, str) and v.strip()]

        start = raw.get("start") or raw.get("startCommand")
        start = None if not (isinstance(start, str) and start.strip()) else start.strip()

        port_raw = raw.get("port") or raw.get("startPort")
        port: int | None = None
        if isinstance(port_raw, int) and 0 < port_raw < 65536:
            port = port_raw
        elif isinstance(port_raw, str) and port_raw.strip().isdigit():
            candidate = int(port_raw.strip())
            if 0 < candidate < 65536:
                port = candidate

        readiness = raw.get("readinessPath") or raw.get("readiness_path") or "/"
        if not (isinstance(readiness, str) and readiness.startswith("/")):
            readiness = "/"

        return cls(
            name=name.strip(),
            kind=kind.strip(),
            bootstrap=as_strings(raw.get("bootstrap") or raw.get("installCommands")),
            build=as_strings(raw.get("build") or raw.get("buildCommands")),
            test=as_strings(raw.get("test") or raw.get("testCommands")),
            start=start,
            port=port,
            readiness_path=readiness,
            evidence=as_strings(raw.get("evidence")),
        )


# ---------------------------------------------------------------------------
# manifesto
# ---------------------------------------------------------------------------


def manifest_path(root: Path) -> Path:
    """Caminho do manifesto de verificação do projeto em ``root``."""
    return Path(root) / _MANIFEST_RELPATH


def load_manifest(root: Path) -> Recipe | None:
    """Carrega a receita salva do manifesto, tolerando arquivo malformado.

    Qualquer problema de leitura/parse/forma devolve ``None`` em vez de
    levantar — um manifesto corrompido degrada para a detecção nova em vez de
    quebrar o `kairos verify`.
    """
    path = manifest_path(root)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    # Aceita tanto o envelope {version, recipe} quanto uma receita nua.
    recipe_raw = manifest.get("recipe", manifest)
    return Recipe.from_dict(recipe_raw)


def save_manifest(root: Path, recipe: Recipe) -> Path:
    """Persiste ``recipe`` como o manifesto de verificação do projeto."""
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": MANIFEST_VERSION,
        "recipe": recipe.to_dict(),
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_or_detect(root: Path) -> tuple[Recipe | None, str]:
    """Retorna ``(receita, origem)`` com origem ``manifest`` ou ``detected``.

    Um manifesto salvo vence sobre a detecção nova — é a fonte de verdade
    editada pelo usuário.
    """
    saved = load_manifest(root)
    if saved is not None:
        return saved, "manifest"
    return detect_recipe(root), "detected"


# ---------------------------------------------------------------------------
# detecção
# ---------------------------------------------------------------------------


def _read_text(root: Path, name: str) -> str | None:
    try:
        return (root / name).read_text(encoding="utf-8")
    except OSError:
        return None


def _read_package_json(root: Path) -> dict[str, Any] | None:
    raw = _read_text(root, "package.json")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def detect_package_manager(root: Path) -> str | None:
    """Detecção de gerenciador de pacotes pela presença de lockfile."""
    candidates = [
        ("pnpm-lock.yaml", "pnpm"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("Pipfile.lock", "pipenv"),
    ]
    for filename, manager in candidates:
        if (root / filename).exists():
            return manager
    return None


def _infer_port_from_command(command: str | None) -> int | None:
    """Inferência de porta a partir do comando de start."""
    if not command:
        return None
    flag = re.search(r"(?:--port|-p)\s+(\d{2,5})", command)
    if flag:
        return int(flag.group(1))
    env = re.search(r"\bPORT=(\d{2,5})\b", command)
    if env:
        return int(env.group(1))
    return None


def _dedupe(values: list[str | None]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        if value and value.strip():
            seen.setdefault(value.strip(), None)
    return list(seen)


# --- Node ----------------------------------------------------------------


def _script_runner(package_manager: str | None, entry: str) -> str:
    if package_manager == "pnpm":
        return f"pnpm {entry}"
    if package_manager == "bun":
        return f"bun run {entry}"
    if package_manager == "yarn":
        return f"yarn {entry}"
    return f"npm run {entry}"


def _detect_node_recipe(root: Path, pkg: dict[str, Any]) -> Recipe:
    raw_scripts = pkg.get("scripts")
    scripts: dict[str, str] = raw_scripts if isinstance(raw_scripts, dict) else {}
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        section = pkg.get(key)
        if isinstance(section, dict):
            deps.update(section)

    package_manager = detect_package_manager(root)

    kind, label, default_port = "node", "Node.js", None
    if "next" in deps:
        kind, label, default_port = "nextjs", "Next.js", 3000
    elif "@sveltejs/kit" in deps:
        kind, label, default_port = "sveltekit", "SvelteKit", 5173
    elif "astro" in deps:
        kind, label, default_port = "astro", "Astro", 4321
    elif "@remix-run/dev" in deps or "@remix-run/react" in deps:
        kind, label, default_port = "remix", "Remix", 3000
    elif "react-scripts" in deps:
        kind, label, default_port = "cra", "Create React App", 3000
    elif "vite" in deps:
        kind, label, default_port = "vite", "Vite", 5173

    install = {
        "pnpm": "pnpm install",
        "bun": "bun install",
        "yarn": "yarn install",
        "npm": "npm install",
    }.get(package_manager or "npm", "npm install")

    start_script = "dev" if scripts.get("dev") else ("start" if scripts.get("start") else None)
    start_body = scripts.get(start_script) if start_script else None
    start = _script_runner(package_manager, start_script) if start_script else None
    port = _infer_port_from_command(start_body) or default_port if start else None

    build = _dedupe(
        [_script_runner(package_manager, s) for s in ("build", "typecheck") if scripts.get(s)]
    )
    test = _dedupe(
        [_script_runner(package_manager, s) for s in ("test", "check", "lint") if scripts.get(s)]
    )

    return Recipe(
        name=label,
        kind=kind,
        bootstrap=[install],
        build=build,
        test=test,
        start=start,
        port=port,
        evidence=_dedupe(
            [
                "Detected package.json",
                f"Package manager: {package_manager}" if package_manager else None,
                f"Scripts: {', '.join(scripts) or '(none)'}",
            ]
        ),
    )


# --- Python --------------------------------------------------------------


def _install_command(package_manager: str | None, pyproject: bool, requirements: bool) -> str:
    if package_manager == "uv":
        return "uv sync"
    if package_manager == "poetry":
        return "poetry install"
    if package_manager == "pipenv":
        return "pipenv install"
    if pyproject and not requirements:
        return "pip install -e ."
    return "pip install -r requirements.txt"


def _app_module(root: Path, flavor: str, *, dotted: bool) -> str:
    if (root / "main.py").exists():
        module = "main"
    elif (root / "app.py").exists():
        module = "app"
    else:
        module = "main" if flavor == "fastapi" else "app"
    if flavor == "fastapi" and dotted:
        return f"{module}:app"
    return f"{module}.py"


def _django_recipe(root: Path, install: str, manage_py: bool, pyproject: bool) -> Recipe:
    return Recipe(
        name="Django",
        kind="django",
        bootstrap=[install],
        test=["python manage.py test"],
        start="python manage.py runserver 0.0.0.0:8000",
        port=8000,
        evidence=_dedupe(
            [
                "Detected manage.py" if manage_py else "Detected Django dependency",
                "Detected pyproject.toml" if pyproject else None,
            ]
        ),
    )


def _fastapi_recipe(root: Path, install: str, has_tests: bool) -> Recipe:
    return Recipe(
        name="FastAPI",
        kind="fastapi",
        bootstrap=[install],
        test=["pytest"] if has_tests else [],
        start=f"uvicorn {_app_module(root, 'fastapi', dotted=True)} --host 0.0.0.0 --port 8000",
        port=8000,
        evidence=["Detected Python project", "Detected FastAPI/Uvicorn dependency"],
    )


def _flask_recipe(root: Path, install: str, has_tests: bool) -> Recipe:
    return Recipe(
        name="Flask",
        kind="flask",
        bootstrap=[install],
        test=["pytest"] if has_tests else [],
        start=f"flask --app {_app_module(root, 'flask', dotted=False)} run --host 0.0.0.0 --port 5000",
        port=5000,
        evidence=["Detected Python project", "Detected Flask dependency"],
    )


def _detect_python_recipe(root: Path) -> Recipe | None:
    pyproject = _read_text(root, "pyproject.toml")
    requirements = _read_text(root, "requirements.txt")
    manage_py = (root / "manage.py").exists()
    if not (pyproject or requirements or manage_py or (root / "setup.py").exists()):
        return None

    lower = f"{pyproject or ''}\n{requirements or ''}".lower()
    install = _install_command(
        detect_package_manager(root), pyproject is not None, requirements is not None
    )
    has_tests = (root / "tests").exists()

    if manage_py or "django" in lower:
        return _django_recipe(root, install, manage_py, pyproject is not None)
    if "fastapi" in lower or "uvicorn" in lower:
        return _fastapi_recipe(root, install, has_tests)
    if "flask" in lower:
        return _flask_recipe(root, install, has_tests)
    return Recipe(
        name="Python project",
        kind="python",
        bootstrap=[install],
        test=["pytest"] if has_tests else ["python -m unittest discover"],
        evidence=["Detected Python project"],
    )


# --- Go / Rust / Java / Make / compose -----------------------------------


def _detect_go_recipe(root: Path) -> Recipe | None:
    if not (root / "go.mod").exists():
        return None
    return Recipe(
        name="Go project",
        kind="go",
        build=["go build ./..."],
        test=["go test ./..."],
        start="go run ." if (root / "main.go").exists() else None,
        evidence=["Detected go.mod"],
    )


def _detect_rust_recipe(root: Path) -> Recipe | None:
    if not (root / "Cargo.toml").exists():
        return None
    return Recipe(
        name="Rust project",
        kind="rust",
        build=["cargo build"],
        test=["cargo test"],
        start="cargo run" if (root / "src" / "main.rs").exists() else None,
        evidence=["Detected Cargo.toml"],
    )


def _detect_java_recipe(root: Path) -> Recipe | None:
    if (root / "pom.xml").exists():
        return Recipe(
            name="Maven project",
            kind="maven",
            build=["mvn package"],
            test=["mvn test"],
            evidence=["Detected pom.xml"],
        )
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        gradle = "./gradlew" if (root / "gradlew").exists() else "gradle"
        return Recipe(
            name="Gradle project",
            kind="gradle",
            build=[f"{gradle} build"],
            test=[f"{gradle} test"],
            evidence=["Detected Gradle build file"],
        )
    return None


_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s|$)")


def _parse_make_targets(raw: str) -> list[str]:
    targets = []
    for line in raw.splitlines():
        match = _MAKE_TARGET_RE.match(line)
        if match:
            targets.append(match.group(1))
    return targets


def _detect_make_recipe(root: Path) -> Recipe | None:
    makefile = _read_text(root, "Makefile")
    if makefile is None:
        return None
    targets = _parse_make_targets(makefile)

    def pick(names: list[str]) -> str | None:
        for name in names:
            if name in targets:
                return name
        return None

    install = pick(["install", "setup", "bootstrap"])
    build = pick(["build", "compile"])
    test = pick(["test", "check"])
    run = pick(["run", "start", "serve", "dev"])

    return Recipe(
        name="Makefile-driven project",
        kind="make",
        bootstrap=[f"make {install}"] if install else [],
        build=[f"make {build}"] if build else [],
        test=[f"make {test}"] if test else [],
        start=f"make {run}" if run else None,
        evidence=["Detected Makefile", f"Targets: {', '.join(targets) or '(none)'}"],
    )


_COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)


def _detect_compose_recipe(root: Path) -> Recipe | None:
    compose_file = next((f for f in _COMPOSE_FILES if (root / f).exists()), None)
    if compose_file is None:
        return None
    return Recipe(
        name="docker-compose project",
        kind="compose",
        build=["docker compose build"],
        start="docker compose up",
        evidence=[f"Detected {compose_file}"],
    )


def detect_recipe(root: Path) -> Recipe | None:
    """Detecta uma receita de verificação para o projeto em ``root``.

    Ordem de detecção espelha o legado: package.json vence, depois Python,
    Go, Rust, Java, Makefile e compose. Devolve ``None`` quando nada é
    reconhecível.
    """
    root = Path(root)
    pkg = _read_package_json(root)
    if pkg is not None:
        return _detect_node_recipe(root, pkg)
    return (
        _detect_python_recipe(root)
        or _detect_go_recipe(root)
        or _detect_rust_recipe(root)
        or _detect_java_recipe(root)
        or _detect_make_recipe(root)
        or _detect_compose_recipe(root)
    )
