"""Entrypoint explícito do ensaio temporário; não faz parte do startup Kairos."""

from scripts.e03.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
