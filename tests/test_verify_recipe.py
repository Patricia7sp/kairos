"""Detecção/parse de receita e manifesto do `kairos verify`."""

from __future__ import annotations

import json

from kairos_cli.verify_recipe import (
    Recipe,
    detect_package_manager,
    detect_recipe,
    load_manifest,
    load_or_detect,
    manifest_path,
    save_manifest,
)


def _write(root, name, data="[ignored]"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    else:
        path.write_text(data, encoding="utf-8")
    return path


# --- package manager ----------------------------------------------------


def test_detect_package_manager_cada_um_sozinho(tmp_path):
    casos = {
        "pnpm-lock.yaml": "pnpm",
        "bun.lock": "bun",
        "bun.lockb": "bun-binary",
        "yarn.lock": "yarn",
        "package-lock.json": "npm",
        "uv.lock": "uv",
        "poetry.lock": "poetry",
        "Pipfile.lock": "pipenv",
    }
    for name, expected in casos.items():
        sub = tmp_path / expected.replace("-", "_")
        sub.mkdir()
        (sub / name).touch()
        assert detect_package_manager(sub) == ("bun" if "bun" in name else expected)


def test_sem_lockfile_nao_detecta_gerenciador(tmp_path):
    assert detect_package_manager(tmp_path) is None


# --- Node ---------------------------------------------------------------


def test_detect_node_com_scripts_e_package_manager(tmp_path):
    _write(
        tmp_path,
        "package.json",
        {"name": "app", "scripts": {"dev": "vite", "build": "vite build", "test": "vitest run"}},
    )
    _write(tmp_path, "yarn.lock")
    recipe = detect_recipe(tmp_path)
    assert recipe is not None
    # sem dependência de framework o kind declarado é Node.js
    assert recipe.kind == "node"
    assert recipe.bootstrap == ["yarn install"]
    assert recipe.build == ["yarn build"]
    assert recipe.test == ["yarn test"]
    assert recipe.start == "yarn dev"


def test_detect_vite_pela_dependencia(tmp_path):
    _write(
        tmp_path,
        "package.json",
        {"name": "app", "devDependencies": {"vite": "5"}, "scripts": {"dev": "vite"}},
    )
    _write(tmp_path, "yarn.lock")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "vite"
    assert recipe.port == 5173
    assert recipe.start == "yarn dev"


def test_detect_nextjs_usa_padrao_3000(tmp_path):
    _write(
        tmp_path,
        "package.json",
        {
            "name": "app",
            "dependencies": {"next": "14"},
            "scripts": {"dev": "next dev", "build": "next build", "lint": "next lint"},
        },
    )
    _write(tmp_path, "package-lock.json")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "nextjs"
    assert recipe.bootstrap == ["npm install"]
    assert recipe.build == ["npm run build"]
    assert recipe.test == ["npm run lint"]
    assert recipe.port == 3000


def test_port_inferido_do_comando_do_script(tmp_path):
    _write(
        tmp_path,
        "package.json",
        {"name": "app", "scripts": {"dev": "next dev --port 7337", "build": "next build"}},
    )
    _write(tmp_path, "package-lock.json")
    recipe = detect_recipe(tmp_path)
    assert recipe.port == 7337


def test_sem_script_dev_nao_inventa_start(tmp_path):
    _write(tmp_path, "package.json", {"name": "app", "scripts": {"build": "tsc"}})
    _write(tmp_path, "package-lock.json")
    recipe = detect_recipe(tmp_path)
    assert recipe.start is None
    assert recipe.port is None
    assert recipe.bootstrap == ["npm install"]


# --- Python -------------------------------------------------------------


def test_detect_django_pelo_manage(tmp_path):
    _write(tmp_path, "manage.py")
    _write(tmp_path, "requirements.txt", "django\n")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "django"
    assert recipe.test == ["python manage.py test"]
    assert recipe.start == "python manage.py runserver 0.0.0.0:8000"
    assert recipe.port == 8000


def test_detect_fastapi_com_app(tmp_path):
    _write(tmp_path, "pyproject.toml", "[dependencies]\nfastapi\n")
    _write(tmp_path, "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    (tmp_path / "tests").mkdir()
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "fastapi"
    assert recipe.test == ["pytest"]
    assert "uvicorn" in recipe.start
    assert recipe.port == 8000


def test_detect_flask_com_app(tmp_path):
    _write(tmp_path, "requirements.txt", "flask\n")
    _write(tmp_path, "app.py", "from flask import Flask\napp = Flask(__name__)\n")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "flask"
    assert "flask --app app.py run" in recipe.start
    assert recipe.port == 5000


def test_detect_python_plano_com_pyproject_uv(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\n')
    _write(tmp_path, "uv.lock")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "python"
    assert recipe.bootstrap == ["uv sync"]


def test_detect_python_sem_requirements_usa_pip_editavel(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="x"\n')
    recipe = detect_recipe(tmp_path)
    assert recipe.bootstrap == ["pip install -e ."]


# --- Go / Rust / Java / Make / compose ----------------------------------


def test_detect_go(tmp_path):
    _write(tmp_path, "go.mod", "module x\n")
    _write(tmp_path, "main.go", "package main\n")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "go"
    assert recipe.build == ["go build ./..."]
    assert recipe.test == ["go test ./..."]
    assert recipe.start == "go run ."


def test_detect_rust(tmp_path):
    _write(tmp_path, "Cargo.toml", '[package]\nname="x"\n')
    _write(tmp_path, "src/main.rs", "fn main() {}\n")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "rust"
    assert recipe.test == ["cargo test"]
    assert recipe.start == "cargo run"


def test_detect_maven(tmp_path):
    _write(tmp_path, "pom.xml", "<project/>\n")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "maven"
    assert recipe.build == ["mvn package"]


def test_detect_gradle_usa_wrapper(tmp_path):
    _write(tmp_path, "build.gradle", "group='x'\n")
    _write(tmp_path, "gradlew")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "gradle"
    assert recipe.build == ["./gradlew build"]


def test_detect_make_extrai_alvos(tmp_path):
    _write(
        tmp_path,
        "Makefile",
        "install:\n\techo ok\nbuild:\n\techo ok\ntest:\n\techo ok\nrun:\n\techo ok\n",
    )
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "make"
    assert recipe.bootstrap == ["make install"]
    assert recipe.build == ["make build"]
    assert recipe.test == ["make test"]
    assert recipe.start == "make run"


def test_detect_compose(tmp_path):
    _write(tmp_path, "compose.yml", "services: {}\n")
    recipe = detect_recipe(tmp_path)
    assert recipe.kind == "compose"
    assert recipe.build == ["docker compose build"]
    assert recipe.start == "docker compose up"


def test_detect_nada(tmp_path):
    assert detect_recipe(tmp_path) is None


# --- manifesto ----------------------------------------------------------


def test_save_load_roundtrip(tmp_path):
    recipe = Recipe(
        "App", kind="node", bootstrap=["npm install"], build=["npm build"], test=["npm test"]
    )
    path = save_manifest(tmp_path, recipe)
    assert path == manifest_path(tmp_path)
    present = (tmp_path / ".kairos" / "environment.json").exists()
    assert present
    loaded = load_manifest(tmp_path)
    assert loaded is not None
    assert loaded.to_dict() == recipe.to_dict()


def test_load_manifest_tolerante_a_corrompido(tmp_path):
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "environment.json").write_text("{isto nao é json", encoding="utf-8")
    assert load_manifest(tmp_path) is None


def test_load_manifest_tolerante_a_forma_errada(tmp_path):
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "environment.json").write_text(
        json.dumps({"recipe": {"name": ""}}), encoding="utf-8"
    )
    assert load_manifest(tmp_path) is None


def test_manifest_vence_deteccao(tmp_path):
    _write(tmp_path, "package.json", {"name": "app", "scripts": {"build": "tsc"}})
    manual = Recipe("Manual", kind="node", test=["echo manual"])
    save_manifest(tmp_path, manual)
    recipe, source = load_or_detect(tmp_path)
    assert source == "manifest"
    assert recipe.to_dict() == manual.to_dict()


def test_sem_manifest_detecta(tmp_path):
    _write(tmp_path, "Cargo.toml", '[package]\nname="x"\n')
    recipe, source = load_or_detect(tmp_path)
    assert source == "detected"
    assert recipe.kind == "rust"


def test_from_dict_tolerante_a_shape_invalido(tmp_path):
    assert Recipe.from_dict(None) is None
    assert Recipe.from_dict(42) is None
    assert Recipe.from_dict([]) is None
    assert Recipe.from_dict({"name": "  "}) is None


def test_from_dict_aceita_aliases_do_legado():
    raw = {
        "appLabel": "Legacy",
        "appKind": "node",
        "installCommands": ["npm install"],
        "buildCommands": ["npm build"],
        "testCommands": "npm test",
        "startCommand": "npm run start",
        "startPort": "3000",
        "readiness_path": "/health",
        "evidence": ["e1"],
    }
    recipe = Recipe.from_dict(raw)
    assert recipe is not None
    assert recipe.name == "Legacy"
    assert recipe.kind == "node"
    assert recipe.bootstrap == ["npm install"]
    assert recipe.test == ["npm test"]
    assert recipe.start == "npm run start"
    assert recipe.port == 3000
    assert recipe.readiness_path == "/health"
    assert recipe.evidence == ["e1"]
