"""Comando `kairos dump` — exportação estruturada de fontes locais reais."""

import json
import os
from pathlib import Path


async def run_dump(home: Path, args) -> int:
    from kairos_observability.diagnostics import read_diagnostics

    home = Path(home)
    report = await read_diagnostics(home)
    report["scope"] = "local_only"

    config_path = Path(os.environ.get("KAIROS_CONFIG_PATH", str(home / "config.yaml")))
    source_lines = 0
    skills_dir = home / "skills"
    cron_dir = home / "cron"
    journal_dir = home / "journal"
    for d in (skills_dir, cron_dir, journal_dir):
        if d.is_dir():
            for _ in d.rglob("*"):
                if _.is_file():
                    source_lines += 1

    total_size = (
        sum(p.stat().st_size for p in home.rglob("*") if p.is_file()) if home.exists() else 0
    )
    report["sources"] = {
        "home": str(home),
        "home_size_bytes": total_size,
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "skills_dir": str(skills_dir),
        "skills_dir_exists": skills_dir.is_dir(),
        "cron_dir": str(cron_dir),
        "cron_dir_exists": cron_dir.is_dir(),
        "journal_dir": str(journal_dir),
        "journal_dir_exists": journal_dir.is_dir(),
        "source_files": source_lines,
    }

    as_json = getattr(args, "json", False)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Dump local de {home}")
        print(f"  config: {config_path} ({'existe' if config_path.exists() else 'ausente'})")
        print(f"  skills: {skills_dir} ({'existe' if skills_dir.is_dir() else 'ausente'})")
        print(f"  cron:   {cron_dir} ({'existe' if cron_dir.is_dir() else 'ausente'})")
        print(f"  journal:{journal_dir} ({'existe' if journal_dir.is_dir() else 'ausente'})")
        print(f"  fontes: {source_lines} arquivos; {total_size} bytes no home")
        print(f"  estado: {report['state']}")
    return 0 if report["state"] == "complete" else 1
