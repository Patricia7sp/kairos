import asyncio
import hashlib
import json
import multiprocessing
import os
import queue
import time
from contextlib import contextmanager

import pytest
from starlette.requests import Request

from kairos_runtime.reviews import canonical_json
from kairos_tools.authorization import AuditAuthority, AuthorityDenied
from kairos_web.audit_authority import dispatch_capture

REV = "a" * 40
ORIGIN = "https://kairos.example.test"


def request(cookie=True, origin=ORIGIN, header=False):
    from kairos_web.server import SESSION_COOKIE, SESSION_TOKEN, TOKEN_HEADER

    headers = [(b"origin", origin.encode()), (b"content-type", b"application/json")]
    if cookie:
        headers.append((b"cookie", f"{SESSION_COOKIE}={SESSION_TOKEN}".encode()))
    if header:
        headers.append((TOKEN_HEADER.lower().encode(), SESSION_TOKEN.encode()))
    return {"type": "http", "method": "POST", "headers": headers, "query_string": b""}


def catalog(path, content):
    root = path / REV / "workspace"
    path.mkdir(mode=0o700)
    root.parent.mkdir(mode=0o755)
    root.mkdir(mode=0o755)
    (root / "hello").write_bytes(content)
    digest = hashlib.sha256(
        canonical_json(
            [{"path": "hello", "mode": 0o644, "sha256": hashlib.sha256(content).hexdigest()}]
        )
    ).hexdigest()
    (root.parent / "project.json").write_text(
        json.dumps(
            {"schema_version": 1, "revision": REV, "name": "same", "baseline_fingerprint": digest}
        )
    )
    (root.parent / "project.json").chmod(0o644)
    return root


@pytest.fixture
def context(tmp_path):
    home = tmp_path / "private"
    home.mkdir(mode=0o700)
    authority = AuditAuthority(home)
    authority.initialize()
    data = b"synthetic" * 15000
    root = catalog(tmp_path / "catalog", data)
    project = authority.register(root.parent.parent, REV)
    grant = authority.grant("web:operador", project, REV, expires=int(time.time()) + 120)
    return authority, root, project, grant, queue.Queue(maxsize=1), data


def dispatch(ctx, **changes):
    auth, _, project, grant, box, _ = ctx
    scope = changes.pop("request", request())

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(
                {"project": project, "revision": REV, "grant": grant} | changes
            ).encode(),
        }

    asyncio.run(dispatch_capture(Request(scope, receive), authority=auth, origin=ORIGIN, inbox=box))


def test_entrega_bytes_privados_sem_reabrir_origem(context):
    _, root, project, _, box, data = context
    dispatch(context)
    (root / "hello").unlink()
    snapshot = box.get_nowait()
    assert (snapshot.project, snapshot.revision) == (project, REV)
    assert snapshot.contents["hello"] == data
    context[0].revoke(context[3])
    with pytest.raises(AuthorityDenied):
        dispatch(context)
    assert box.empty()
    with pytest.raises(TypeError):
        snapshot.contents["hello"] = b""


def test_recusas_antes_de_enumerar_ou_ler(context, monkeypatch):
    auth, root, _, _, box, data = context
    other = catalog(root.parents[2] / "other", data)
    other_id = auth.register(other.parent.parent, REV)
    from kairos_filesystem import tree

    monkeypatch.setattr(tree.os, "scandir", lambda *_: pytest.fail("enumerou o alvo recusado"))
    monkeypatch.setattr(tree, "_read_file", lambda *a, **kw: pytest.fail("leu conteúdo recusado"))
    for change in (
        {"request": request(False, header=True)},
        {"request": request(origin="https://other.test")},
        {"project": other_id},
        {"revision": "b" * 40},
        {"principal": "web:operador"},
        {"grant": auth.grant("outro", context[2], REV, expires=int(time.time()) + 30)},
    ):
        with pytest.raises(AuthorityDenied):
            dispatch(context, **change)
    auth.revoke(context[3])
    with pytest.raises(AuthorityDenied):
        dispatch(context)
    assert box.empty()


def _revoke_when_ready(home, ref, ready, done):
    if not ready.wait(10):
        raise TimeoutError
    AuditAuthority(home).revoke(ref)
    done.set()


@pytest.mark.parametrize("stage", ["admission", "block", "delivery"])
def test_revogacao_confirmada_em_outro_processo(context, monkeypatch, stage):
    from kairos_tools import authorization

    auth, root, _, ref, box, _ = context
    mp = multiprocessing.get_context("spawn")
    ready, done = mp.Event(), mp.Event()
    proc = mp.Process(target=_revoke_when_ready, args=(auth.home, ref, ready, done))
    inode = (root / "hello").stat().st_ino
    real_read = os.read
    chunks = []

    def read(fd, n):
        data = real_read(fd, n)
        if os.fstat(fd).st_ino == inode:
            chunks.append(len(data))
        return data

    monkeypatch.setattr(os, "read", read)

    def revoke():
        ready.set()
        assert done.wait(10), "revogação bloqueada durante a pausa entre operações"

    real_guard = auth._guard

    @contextmanager
    def guard(*args):
        with real_guard(*args) as target:
            yield target
        if not ready.is_set() and (stage == "admission" or (stage == "block" and chunks)):
            revoke()

    monkeypatch.setattr(auth, "_guard", guard)
    real_fingerprint = authorization.fingerprint_files

    def fingerprint(files):
        value = real_fingerprint(files)
        if stage == "delivery":
            revoke()
        return value

    monkeypatch.setattr(authorization, "fingerprint_files", fingerprint)
    proc.start()
    try:
        with pytest.raises(AuthorityDenied):
            dispatch(context)
        assert done.is_set() and box.empty()
        if stage == "block":
            assert len(chunks) == 1 and 0 < chunks[0] < len(context[-1])
        elif stage == "admission":
            assert not chunks
        else:
            assert sum(chunks) == len(context[-1])
    finally:
        ready.set()
        proc.join(10)
        if proc.is_alive():
            proc.kill()
            proc.join()
        assert proc.exitcode == 0


@pytest.mark.parametrize("change", ["expired", "policy", "db_missing", "lock_replaced", "full"])
def test_falha_fechada(context, monkeypatch, change):
    auth, _, _, _, box, _ = context
    if change == "expired":
        monkeypatch.setattr(time, "time", lambda: 2**34)
    elif change == "policy":
        auth.invalidate_policy()
    elif change == "db_missing":
        db = auth.home / "audit-authority.sqlite3"
        db.unlink()
    elif change == "lock_replaced":
        lock = auth.home / "audit-authority.lock"
        lock.rename(lock.with_suffix(".old"))
        lock.touch(mode=0o600)
    else:
        box.put_nowait("ocupada")
    with pytest.raises(AuthorityDenied):
        dispatch(context)
    if change == "full":
        assert box.get_nowait() == "ocupada"
    if change == "db_missing":
        assert not db.exists()
    assert box.empty()


def test_troca_da_raiz_ou_conteudo_recusa(context):
    _, root, _, _, box, data = context
    (root / "hello").write_bytes(b"divergente")
    with pytest.raises(AuthorityDenied):
        dispatch(context)
    root.rename(root.with_name("anterior"))
    root.mkdir()
    (root / "hello").write_bytes(data)
    with pytest.raises(AuthorityDenied):
        dispatch(context)
    assert box.empty()


def test_manifesto_gravavel_por_terceiros_recusa_sem_ler_workspace(context, monkeypatch):
    (_, root, _, _, box, _) = context
    (root.parent / "project.json").chmod(0o666)
    monkeypatch.setattr(os, "scandir", lambda *_: pytest.fail("workspace enumerado"))
    with pytest.raises(AuthorityDenied):
        dispatch(context)
    assert box.empty()


def test_lock_cobre_operacao_de_io_e_publicacao(context, monkeypatch):
    import fcntl

    auth, root, _, _, box, _ = context
    inode = (root / "hello").stat().st_ino
    observed = set()

    def locked(stage):
        with (
            (auth.home / "audit-authority.lock").open("rb") as independent,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(independent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed.add(stage)

    real_read = os.read

    def read(fd, n):
        if os.fstat(fd).st_ino == inode:
            locked("read")
        return real_read(fd, n)

    real_put = box.put_nowait

    def put(item):
        locked("delivery")
        real_put(item)

    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(box, "put_nowait", put)
    dispatch(context)
    assert observed == {"read", "delivery"} and not box.empty()


@pytest.mark.parametrize("phase", ["admission", "delivery"])
@pytest.mark.parametrize("change", ["expiry", "policy", "root"])
def test_revalida_vinculos_nas_duas_fronteiras(context, monkeypatch, phase, change):
    from kairos_tools import authorization

    auth, root, _, _, box, data = context

    def alter():
        if change == "policy":
            auth.invalidate_policy()
        elif change == "expiry":
            monkeypatch.setattr(time, "time", lambda: 2**34)
        else:
            root.rename(root.with_name("original"))
            root.mkdir()
            (root / "hello").write_bytes(data)

    if phase == "admission":
        original = auth._guard
        first = True

        @contextmanager
        def after_dispatch(*args):
            nonlocal first
            with original(*args) as target:
                yield target
            if first:
                first = False
                alter()

        monkeypatch.setattr(auth, "_guard", after_dispatch)
        monkeypatch.setattr(
            os, "scandir", lambda *_: pytest.fail("enumerou workspace sem admissão")
        )
    else:
        original_hash = authorization.fingerprint_files

        def before_delivery(files):
            alter()
            return original_hash(files)

        monkeypatch.setattr(authorization, "fingerprint_files", before_delivery)
    with pytest.raises(AuthorityDenied):
        dispatch(context)
    assert box.empty()
