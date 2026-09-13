"""Gateway lifecycle guard for cron job creation.

An agent working inside the kairos gateway could schedule a cron job whose
prompt calls for restarting the very process that runs it.  When the cron
fires, the gateway dies, the supervisor (systemd ``Restart=`` / launchd
``KeepAlive`` / container orchestrator) revives it, auto-resume picks up the
session, and the resumed turn re-runs the same logic — a SIGTERM-respawn loop
until it is manually broken.

This module rejects cron job specs whose **prompt** or **monitor script**
contains a direct shell-level gateway-lifecycle command.  It is enforced where
the job is created — ``JobStore.create`` and ``JobStore.set_monitor`` — which
covers every write path in kairos: the ``kairos cron create`` CLI subcommand
and the ``POST /api/cron/jobs`` / ``PUT /api/cron/jobs/{id}/monitor`` web
endpoints (the legacy agent ``cronjob`` tool surface does not exist in kairos;
the web API is its narrow waist).

The pattern is intentionally **command-shaped**: it anchors on a concrete
command identifier (``kairos gateway``, ``kairos restart|stop``,
``hermes gateway``, ``launchctl`` / ``systemctl`` / ``p?kill`` against a
gateway unit, ``docker restart|stop`` against a kairos container) so it cannot
fire on prose.  A cron ``prompt`` is fed to a future LLM, not a shell, so an
over-broad substring match on English (the previous guard matched bare
``pkill`` and ``docker restart``) would produce a high false-positive rate
without preventing the actual foot-gun, which requires a real command shape.

Enforcement is deliberately **ingestion-time only**: ``JobStore._validate_job``
does not re-run this check when re-reading a stored document, otherwise a job
created before a guard upgrade (e.g. storing ``launchctl kickstart …
ai.hermes.gateway`` under the older, narrower guard) would make the entire
document unreadable on the next boot.  Content policy applies when content
enters, not when a legitimately stored job is replayed.
"""

from __future__ import annotations

import re
import shlex

LIFECYCLE_MESSAGE = (
    "cron job contém um comando que reiniciaria o gateway. "
    "Um job assim mata o processo que o executa: a execução nunca alcança "
    "estado terminal durável, é reconciliada como 'unknown' no boot e dispara "
    "de novo — laço de reinício disfarçado de automação. Rode o comando num "
    "shell fora do gateway, ou remova-o da instrução ou do monitor do job."
)


class LifecycleGuardError(ValueError):
    """Job que reiniciaria o gateway."""


#: Shell-level command shapes that target the gateway lifecycle.  Each branch
#: is anchored on a concrete command identifier so a match can only fire on
#: actual shell-command-shaped strings, not on prose.
_GATEWAY_LIFECYCLE_PATTERN = re.compile(
    r"(?i)"
    # Branch A: the canonical foot-gun — `kairos (gateway) restart|stop` and
    # `hermes gateway restart|stop`. `start` is intentionally excluded:
    # starting a gateway from inside a gateway is benign (a no-op or "already
    # running" error), and a legitimate cron job might launch a sibling.
    r"(?:kairos\s+(?:gateway\s+)?(?:restart|stop)|hermes\s+gateway\s+(?:restart|stop))"
    # Branch B: launchctl ops against a gateway label.  Requiring the gateway
    # identifier prevents blocking unrelated services (`launchctl unload
    # ai.hermes.update-checker.plist`).
    r"|(?:launchctl\s+(?:kickstart|unload|load|stop|restart|submit|bootstrap)\b[^\n]*\b(?:hermes[.\-]?gateway|ai\.hermes|kairos))"
    # Branch C: systemctl ops against a gateway unit.
    r"|(?:systemctl\s+(?:-\S+\s+)*(?:restart|stop|start)\b[^\n]*\b(?:hermes[.\-]?gateway|kairos))"
    # Branch D: pkill / kill / killall targeting the gateway process. For
    # kairos the process identifier IS the target; for hermes both token
    # orders appear in real reproductions.
    r"|(?:(?:p?kill|killall)\b[^\n]*\bkairos\b)"
    r"|(?:(?:p?kill|killall)\b[^\n]*\bhermes\b[^\n]*\bgateway)"
    r"|(?:(?:p?kill|killall)\b[^\n]*\bgateway\b[^\n]*\bhermes)"
    # Branch E: docker restart/stop against a kairos container.
    r"|(?:docker\s+(?:restart|stop)\b[^\n]*\bkairos\b)"
)


# A backslash immediately followed by a newline is a POSIX shell line
# continuation — the shell joins the two lines before parsing.  Every branch
# above uses `[^\n]*` between its verb and the gateway identifier so a match
# can't span unrelated lines of a longer prompt, but a real multi-line shell
# invocation split across continuation lines would otherwise slip past.
# Collapse continuations to a single space before matching, mirroring what the
# shell itself does.
_SHELL_LINE_CONTINUATION = re.compile(r"\\\r?\n[ \t]*")


def contains_gateway_lifecycle_command(text: str) -> bool:
    """True if *text* contains a gateway lifecycle command pattern."""
    if not text:
        return False
    normalized = _SHELL_LINE_CONTINUATION.sub(" ", text)
    return bool(_GATEWAY_LIFECYCLE_PATTERN.search(normalized))


#: Executables whose arguments are DATA, not commands: search patterns, SQL
#: statements, log filters.  None of these can execute their argument text, so
#: a lifecycle-shaped string inside their arguments is diagnostics, not a
#: lifecycle command.  Deliberately conservative: no `awk` (system()), no
#: `sed` (`s///e`), no `echo`/`printf` (routinely piped into a shell).
_DATA_SINK_EXECUTABLES = frozenset(
    {"grep", "egrep", "fgrep", "rg", "ag", "ack", "journalctl", "sqlite3", "psql"}
)
#: Argument shapes that can smuggle execution back INTO a data sink: command
#: and process substitution anywhere, sqlite3 dot-commands (`.shell ...`),
#: psql backslash escapes (`\! ...`).  Any hit disables masking for the whole
#: segment — fail closed to the plain regex verdict.
_UNSAFE_DATA_ARG_MARKERS = ("`", "$(", "<(", ">(", "\\!")
#: A data sink piped into a shell/interpreter can feed matched lines straight
#: to execution (`grep 'systemctl restart kairos' f | sh`); never mask such a
#: line.
_PIPE_TO_INTERPRETER = re.compile(
    r"\|\s*&?\s*(?:sudo\s+)?(?:sh|bash|dash|ksh|zsh|xargs|eval|source)\b"
)
_CONTROL_CHARS = frozenset(";&|()")


def _mask_data_sink_arguments(text: str) -> str:
    """Replace data-sink executables' arguments with a neutral placeholder.

    The lifecycle regex is command-shaped, but it cannot tell an EXECUTED
    ``systemctl restart kairos`` from the same characters appearing as *data*
    — a grep/rg pattern, a journalctl filter, a SQL string literal passed to
    sqlite3/psql.  Those diagnostics prompts were being rejected (false
    positives), e.g.::

        grep -c 'systemctl restart kairos' /var/log/syslog
        sqlite3 db "SELECT msg WHERE msg LIKE '%pkill -f kairos%'"

    This masker shell-tokenizes each line and, for command segments whose
    executable is a known data sink, replaces every argument with ``arg``.
    The caller then re-runs the lifecycle regex on the masked text: a match
    that survives masking sits OUTSIDE any data argument and is a real command.

    Strictly fail-closed: masking is skipped whenever the line pipes into a
    shell or interpreter, any argument carries an execution-capable marker, or
    the line cannot be tokenized at all.  Masking can therefore only ever ALLOW
    a command the plain regex would have blocked — never block one it would
    have allowed — so it runs solely as a second-pass exemption check.
    """
    lines_out: list[str] = []
    changed = False
    for line in text.splitlines() or [text]:
        if _PIPE_TO_INTERPRETER.search(line) or not line.strip():
            lines_out.append(line)
            continue
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
        except ValueError:
            lines_out.append(line)
            continue

        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token and set(token) <= _CONTROL_CHARS:
                segments.append(current)
                segments.append([token])
                current = []
                continue
            current.append(token)
        segments.append(current)

        rebuilt: list[str] = []
        for segment in segments:
            if not segment:
                continue
            index = None
            for i, token in enumerate(segment):
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
                    continue
                index = i
                break
            if index is not None and segment[index].rsplit("/", 1)[-1] in _DATA_SINK_EXECUTABLES:
                arguments = segment[index + 1 :]
                if not any(
                    arg.startswith(".") or any(marker in arg for marker in _UNSAFE_DATA_ARG_MARKERS)
                    for arg in arguments
                ):
                    changed = True
                    rebuilt.extend(segment[: index + 1])
                    rebuilt.extend("arg" for _ in arguments)
                    continue
            rebuilt.extend(segment)
        lines_out.append(" ".join(rebuilt))
    if not changed:
        return text
    return "\n".join(lines_out)


def _lifecycle_command_scan_with_data_exemption(text: str) -> bool:
    """Lifecycle-regex scan that exempts matches living inside data arguments.

    Two-pass: the cheap regex first (the overwhelmingly common no-match case
    pays nothing extra); on a raw match, re-scan with data-sink arguments
    masked out.  Only a match that survives masking — i.e. one in actual
    command position — blocks.
    """
    if not contains_gateway_lifecycle_command(text):
        return False
    normalized = _SHELL_LINE_CONTINUATION.sub(" ", text)
    return contains_gateway_lifecycle_command(_mask_data_sink_arguments(normalized))


def check_gateway_lifecycle(prompt: str | None, script: str | None = None) -> None:
    """Raise ``LifecycleGuardError`` if *prompt* or *script* contains a
    command-shaped gateway-lifecycle pattern.

    ``prompt`` and ``script`` are scanned together so a job cannot slip
    through by splitting the command across its instruction and its monitor.

    Callers let the exception propagate: the CLI prints it in red and exits 1,
    the web API maps it to ``422``.
    """
    combined = prompt or ""
    if script:
        combined = f"{combined}\n{script}"
    if _lifecycle_command_scan_with_data_exemption(combined):
        raise LifecycleGuardError(LIFECYCLE_MESSAGE)
