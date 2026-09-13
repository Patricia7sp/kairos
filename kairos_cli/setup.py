"""Comando `kairos setup` — inicializa idempotentemente o home do Kairos."""
import asyncio
import json
import os
import secrets
import stat
from pathlib import Path


async def run_setup(home: Path, args) -> int:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)

    token_path = home / "web-token"
    created_token = False
    if not token_path.exists():
        created_token = True
        token = secrets.token_urlsafe(32)
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token + "\n")

    from kairos_state import connect, read_schema_version, migrate

    db_path = home / "state.db"
    conn = connect(db_path)
    try:
        versao = read_schema_version(conn)
        if versao is None:
            migrate(conn)
            versao = read_schema_version(conn)
    finally:
        conn.close()

    from kairos_cli.config import get_config_path, load_config, save_config

    config_path = get_config_path()
    if not config_path.exists():
        config = load_config() or {"ui": {"theme": "system", "font_size": 14}}
        save_config(config)

    as_json = getattr(args, "json", False)
    if as_json:
        print(json.dumps({"home": str(home), "schema_version": versao, "token_created": created_token}, ensure_ascii=False, indent=2))
    else:
        print(f"Home: {home}")
        print(f"  web-token: {'criado' if created_token else 'já existia'} em {token_path} (0600)")
        print(f"  state.db:  schema {versao}")
        print(f"  config:    {config_path}")
    return 0