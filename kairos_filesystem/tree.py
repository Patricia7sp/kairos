"""Inventários estáveis e mutações de árvores por descritores."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from contextlib import ExitStack, contextmanager

from kairos_filesystem.contract import FilesystemError, TreeCapture, TreeEntry, TreeLimits
from kairos_filesystem.descriptors import check_chain, open_fd, same_entry

MAX_LIMITS = TreeLimits(256 * 1024 * 1024, 256 * 1024 * 1024, 10000, 32)


@contextmanager
def _io_errors():
    try:
        yield
    except OSError:
        raise FilesystemError("io", "Não foi possível acessar a árvore com segurança.") from None


def _require(condition: bool, message: str, kind: str = "input") -> None:
    if not condition:
        raise FilesystemError(kind, message)


def _name(name: str) -> None:
    _require(
        type(name) is str
        and bool(name)
        and name not in (".", "..")
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name,
        "Nome deve identificar um filho imediato.",
    )


def _limits(limits: TreeLimits) -> None:
    _require(isinstance(limits, TreeLimits), "Limites inválidos.")
    _require(
        all(
            type(n) is int and n >= 0
            for n in (limits.total_bytes, limits.file_bytes, limits.entries, limits.depth)
        ),
        "Limites inválidos.",
    )


def observed_mount_id(fd: int) -> int:
    """Identidade de montagem observada, inclusive em bind mounts de mesmo dev."""
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as info:
            values = [line.partition(":")[2].strip() for line in info if line.startswith("mnt_id:")]
        if len(values) != 1 or not values[0].isdigit() or int(values[0]) <= 0:
            raise ValueError
        return int(values[0])
    except (OSError, UnicodeError, ValueError):
        raise FilesystemError("unavailable", "Identidade de montagem indisponível.") from None


def _metadata(fd: int, mount: int) -> os.stat_result:
    value = os.fstat(fd)
    _require(observed_mount_id(fd) == mount, "Ponto de montagem recusado.", "conflict")
    _require(
        stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode), "Entrada especial recusada."
    )
    _require(
        not value.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX), "Modo especial recusado."
    )
    if stat.S_ISREG(value.st_mode):
        _require(value.st_nlink == 1, "Arquivo com hardlinks recusado.")
    try:
        attributes = os.listxattr(fd)
    except (AttributeError, NotImplementedError, OSError):
        raise FilesystemError(
            "unavailable", "Observação de atributos estendidos indisponível."
        ) from None
    _require(not attributes, "ACL ou atributo estendido não preservável.")
    return value


def _check_mount_chain(chain: list[tuple[int, str, int]]) -> None:
    check_chain(chain)
    for parent, name, fd in chain:
        with ExitStack() as stack:
            current = open_fd(stack, name, os.O_PATH, parent=parent)
            opened = os.fstat(fd)
            observed = os.fstat(current)
            _require(
                (opened.st_dev, opened.st_ino, observed_mount_id(fd))
                == (observed.st_dev, observed.st_ino, observed_mount_id(current)),
                "Entrada ou montagem alterada; arquivos preservados.",
                "conflict",
            )


def _stamp(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_file(fd: int, size: int, *, include_contents: bool) -> tuple[str, bytes | None]:
    digest = hashlib.sha256()
    data = bytearray() if include_contents else None
    count = 0
    while True:
        chunk = os.read(fd, min(65536, size - count + 1))
        if not chunk:
            break
        count += len(chunk)
        _require(count <= size, "Arquivo alterado durante leitura.", "conflict")
        digest.update(chunk)
        if data is not None:
            data.extend(chunk)
    _require(count == size, "Arquivo alterado durante leitura.", "conflict")
    return digest.hexdigest(), bytes(data) if data is not None else None


def _scan(stack: ExitStack, parent_fd: int, name: str, limits: TreeLimits, include_contents: bool):
    mount = observed_mount_id(parent_fd)
    entries = []
    contents = {} if include_contents else None
    handles = {}
    chain = []
    total = 0

    def visit(parent: int, component: str, path: str, depth: int):
        nonlocal total
        _require(
            depth <= limits.depth and len(entries) < limits.entries,
            "Limite de entradas ou profundidade excedido.",
        )
        check_chain(chain)
        linked = os.stat(component, dir_fd=parent, follow_symlinks=False)
        _require(
            stat.S_ISDIR(linked.st_mode) or stat.S_ISREG(linked.st_mode),
            "Entrada especial ou link recusado.",
        )
        flags = os.O_RDONLY | os.O_NONBLOCK
        if stat.S_ISDIR(linked.st_mode):
            flags |= os.O_DIRECTORY
        fd = open_fd(stack, component, flags, parent=parent)
        before = _metadata(fd, mount)
        _require(_stamp(linked) == _stamp(before), "Entrada alterada durante abertura.", "conflict")
        same_entry(parent, component, fd)
        if not path:
            _require(stat.S_ISDIR(before.st_mode), "A raiz deve ser um diretório.")
        chain.append((parent, component, fd))
        directory = stat.S_ISDIR(before.st_mode)
        size = 0 if directory else before.st_size
        _require(
            size <= limits.file_bytes and total + size <= limits.total_bytes,
            "Limite de bytes excedido.",
        )
        total += size
        digest = None
        if not directory:
            digest, data = _read_file(fd, size, include_contents=include_contents)
            if contents is not None:
                contents[path] = data
        entry = TreeEntry(
            path,
            "directory" if directory else "file",
            stat.S_IMODE(before.st_mode),
            size,
            digest,
            before.st_dev,
            before.st_ino,
            mount,
        )
        entries.append(entry)
        handles[path] = (parent, component, fd, before)
        if directory:
            for child in sorted(os.listdir(fd)):
                _name(child)
                visit(fd, child, f"{path}/{child}" if path else child, depth + 1)
        _require(
            _stamp(before) == _stamp(_metadata(fd, mount)),
            "Árvore alterada durante leitura.",
            "conflict",
        )
        same_entry(parent, component, fd)
        chain.pop()

    visit(parent_fd, name, "", 0)
    chain = [(parent, component, fd) for parent, component, fd, _before in handles.values()]
    _check_mount_chain(chain)
    for _parent, _component, fd, before in handles.values():
        _require(
            _stamp(before) == _stamp(_metadata(fd, mount)),
            "Árvore alterada durante captura.",
            "conflict",
        )
    return (
        TreeCapture(tuple(sorted(entries, key=lambda entry: entry.path)), contents),
        handles,
        chain,
    )


def _check_path(path: str, edges: dict[str, tuple[int, str, int]]) -> None:
    chain = []
    while path:
        chain.append(edges[path])
        path = path.rpartition("/")[0]
    chain.append(edges[""])
    _check_mount_chain(list(reversed(chain)))


def capture_tree(
    parent_fd: int, name: str, *, limits: TreeLimits, include_contents: bool
) -> TreeCapture:
    _name(name)
    _limits(limits)
    _require(type(include_contents) is bool, "Opção de conteúdo inválida.")
    with _io_errors(), ExitStack() as stack:
        capture, _handles, _chain = _scan(stack, parent_fd, name, limits, include_contents)
        return capture


def validate_capture(
    snapshot: TreeCapture, *, limits: TreeLimits = MAX_LIMITS, require_contents: bool = False
) -> None:
    """Valida provas recebidas antes de abrir ou criar qualquer entrada."""
    _limits(limits)
    _require(
        isinstance(snapshot, TreeCapture) and type(snapshot.entries) is tuple, "Snapshot inválido."
    )
    _require(0 < len(snapshot.entries) <= limits.entries, "Quantidade de entradas inválida.")
    _require(snapshot.contents is None or type(snapshot.contents) is dict, "Conteúdo inválido.")
    _require(not require_contents or snapshot.contents is not None, "Snapshot sem conteúdo.")
    seen = {}
    total = 0
    files = set()
    for entry in snapshot.entries:
        _require(isinstance(entry, TreeEntry), "Entrada inválida.")
        _require(
            type(entry.path) is str and entry.path not in seen, "Caminho duplicado ou inválido."
        )
        if entry.path:
            parts = entry.path.split("/")
            for part in parts:
                _name(part)
            _require(len(parts) <= limits.depth, "Profundidade excedida.")
            parent = entry.path.rpartition("/")[0]
            _require(parent in seen and seen[parent].kind == "directory", "Diretório pai ausente.")
        else:
            _require(not seen and entry.kind == "directory", "Raiz inválida.")
        _require(type(entry.kind) is str and entry.kind in ("directory", "file"), "Tipo inválido.")
        _require(type(entry.mode) is int and 0 <= entry.mode <= 0o777, "Modo inválido.")
        _require(
            type(entry.size) is int and 0 <= entry.size <= limits.file_bytes, "Tamanho inválido."
        )
        _require(
            type(entry.device) is int
            and entry.device >= 0
            and type(entry.inode) is int
            and entry.inode > 0
            and type(entry.mount_id) is int
            and entry.mount_id > 0,
            "Identidade inválida.",
        )
        if entry.kind == "directory":
            _require(entry.size == 0 and entry.sha256 is None, "Diretório com conteúdo inválido.")
        else:
            _require(
                type(entry.sha256) is str
                and re.fullmatch(r"[0-9a-f]{64}", entry.sha256) is not None,
                "Hash inválido.",
            )
            total += entry.size
            _require(total <= limits.total_bytes, "Limite total excedido.")
            files.add(entry.path)
            if snapshot.contents is not None:
                data = snapshot.contents.get(entry.path)
                _require(
                    type(data) is bytes and len(data) == entry.size,
                    "Conteúdo ausente ou tamanho divergente.",
                )
                _require(hashlib.sha256(data).hexdigest() == entry.sha256, "Hash divergente.")
        seen[entry.path] = entry
    _require("" in seen, "Raiz ausente.")
    _require(tuple(seen) == tuple(sorted(seen)), "Inventário fora da ordem canônica.")
    if snapshot.contents is not None:
        _require(set(snapshot.contents) == files, "Conteúdo extra no snapshot.")


def _expected_limits(expected: TreeCapture) -> TreeLimits:
    return TreeLimits(
        sum(e.size for e in expected.entries),
        max(e.size for e in expected.entries),
        len(expected.entries),
        max(len(e.path.split("/")) if e.path else 0 for e in expected.entries),
    )


def _matches(actual: TreeCapture, expected: TreeCapture) -> None:
    _require(
        actual.entries == expected.entries,
        "Árvore divergiu do inventário; arquivos preservados.",
        "conflict",
    )
    if expected.contents is not None:
        _require(actual.contents == expected.contents, "Conteúdo divergiu do snapshot.", "conflict")


def verify_tree(parent_fd: int, name: str, expected: TreeCapture) -> None:
    validate_capture(expected)
    actual = capture_tree(
        parent_fd,
        name,
        limits=_expected_limits(expected),
        include_contents=expected.contents is not None,
    )
    _matches(actual, expected)


def restore_tree(parent_fd: int, name: str, snapshot: TreeCapture) -> TreeCapture:
    _name(name)
    validate_capture(snapshot, require_contents=True)
    with _io_errors(), ExitStack() as stack:
        mount = observed_mount_id(parent_fd)
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            raise FilesystemError("conflict", "Destino ocupado; arquivos preservados.") from None
        root = open_fd(stack, name, os.O_RDONLY | os.O_DIRECTORY, parent=parent_fd)
        _metadata(root, mount)
        edges = {"": (parent_fd, name, root)}
        directories = {"": root}
        for entry in snapshot.entries[1:]:
            parent_path, _, component = entry.path.rpartition("/")
            _check_path(parent_path, edges)
            parent = directories[parent_path]
            if entry.kind == "directory":
                os.mkdir(component, 0o700, dir_fd=parent)
                fd = open_fd(stack, component, os.O_RDONLY | os.O_DIRECTORY, parent=parent)
                directories[entry.path] = fd
            else:
                fd = open_fd(stack, component, os.O_WRONLY | os.O_CREAT | os.O_EXCL, parent=parent)
                _metadata(fd, mount)
                data = memoryview(snapshot.contents[entry.path])
                while data:
                    written = os.write(fd, data[:65536])
                    _require(written > 0, "Gravação incompleta.", "io")
                    data = data[written:]
                os.fchmod(fd, entry.mode)
                os.fsync(fd)
            edges[entry.path] = (parent, component, fd)
        for entry in reversed(snapshot.entries):
            if entry.kind == "directory":
                fd = directories[entry.path]
                _check_path(entry.path, edges)
                os.fchmod(fd, entry.mode)
                os.fsync(fd)
        _check_mount_chain(list(edges.values()))
        os.fsync(parent_fd)
        result = capture_tree(
            parent_fd, name, limits=_expected_limits(snapshot), include_contents=True
        )
        _require(
            [(e.path, e.kind, e.mode, e.size, e.sha256) for e in result.entries]
            == [(e.path, e.kind, e.mode, e.size, e.sha256) for e in snapshot.entries]
            and result.contents == snapshot.contents,
            "Restauração divergente; evidências preservadas.",
            "conflict",
        )
        return result


def delete_verified_tree(parent_fd: int, name: str, expected: TreeCapture) -> None:
    _name(name)
    validate_capture(expected)
    with _io_errors(), ExitStack() as stack:
        actual, handles, _chain = _scan(
            stack, parent_fd, name, _expected_limits(expected), expected.contents is not None
        )
        _matches(actual, expected)
        edges = {path: handle[:3] for path, handle in handles.items()}
        for entry in sorted(
            expected.entries, key=lambda e: (e.path.count("/") + bool(e.path), e.path), reverse=True
        ):
            parent, component, fd, before = handles[entry.path]
            _check_path(entry.path, edges)
            observed = _metadata(fd, entry.mount_id)
            _require(
                (observed.st_dev, observed.st_ino, stat.S_IMODE(observed.st_mode))
                == (entry.device, entry.inode, entry.mode),
                "Entrada substituída; resíduo preservado.",
                "conflict",
            )
            if entry.kind == "file":
                _require(
                    _stamp(observed) == _stamp(before),
                    "Arquivo editado; resíduo preservado.",
                    "conflict",
                )
                same_entry(parent, component, fd)
                os.unlink(component, dir_fd=parent)
            else:
                _require(not os.listdir(fd), "Diretório alterado; resíduo preservado.", "conflict")
                same_entry(parent, component, fd)
                os.rmdir(component, dir_fd=parent)
            del edges[entry.path]
            os.fsync(parent)
