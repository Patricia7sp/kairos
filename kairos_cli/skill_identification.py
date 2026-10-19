"""Diagnóstico público de skills, composto sem recursos de escrita."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from kairos_skills.identification_contract import SkillIdentification
from kairos_skills.mutation_contract import SkillMutationError, validate_name
from kairos_skills.org_policy import observe_org_policy, org_policy_context


def identification_metadata(report: SkillIdentification) -> dict[str, object]:
    return asdict(report)


def run_skill_identification(home: Path, args: argparse.Namespace) -> int:
    try:
        validate_name(args.name)

        from kairos_skills.identification import identify_skill

        with observe_org_policy(org_policy_context(os.environ), home) as policy:
            metadata = identification_metadata(identify_skill(home, args.name, policy=policy))
        if args.json:
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
        else:
            for field, value in metadata.items():
                print(f"{field}: {value}")
            print(
                "removivel é diagnóstico do instante observado; skills remove revalida "
                "as provas e os guards disponíveis."
            )
            print(
                "A proteção organizacional é limitada aos serviços participantes. "
                "Bindings observados sem instalação registrada não criam proteção durável. "
                "A cobertura consta em protection_coverage."
            )
        return 0
    except SkillMutationError as error:
        print("kairos: " + str(error), file=sys.stderr)
        return {"input": 2, "unavailable": 69, "denied": 77}.get(error.kind, 1)
    except (OSError, sqlite3.DatabaseError):
        print(
            "kairos: não foi possível acessar o estado de skills com segurança; arquivos preservados.",
            file=sys.stderr,
        )
        return 1
