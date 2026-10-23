"""Gravação exclusiva e recibo canônico de aquisição local, sem download."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from contextlib import ExitStack
from dataclasses import replace

from kairos_filesystem.contract import TreeCapture, TreeEntry
from kairos_filesystem.tree import _create_directory, restore_tree
from kairos_skills.hub import QuarantineError, scan_quarantined, validated_bundle_files
from kairos_skills.mutation_contract import SkillMutationError, require
from kairos_skills.publication_contract import (
    AcquisitionReceipt,
    PublicationWriter,
    acquisition_scan_hash,
    publication_inventory_hash,
)
from kairos_skills.publication_files import capture_source
from kairos_skills.publication_io import publication_errors
from kairos_skills.publication_session import PublicationSession
from kairos_skills.removal_contract import validate_capture

_LOGGER = logging.getLogger(__name__)


def _bundle_snapshot(bundle):
    try:
        files = validated_bundle_files(bundle)
    except QuarantineError:
        raise SkillMutationError("input", "Bundle de aquisição inválido.") from None
    directories = {""}
    for path in files:
        parts = path.split("/")
        directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    entries = [TreeEntry(path, "directory", 0o700, 0, None, 0, 1, 1) for path in directories]
    entries.extend(
        TreeEntry(path, "file", 0o600, len(data), hashlib.sha256(data).hexdigest(), 0, 1, 1)
        for path, data in files.items()
    )
    snapshot = TreeCapture(tuple(sorted(entries, key=lambda entry: entry.path)), files)
    validate_capture(snapshot, contents=True)
    return snapshot


class SkillAcquisitionService:
    def __init__(self, session):
        require(type(session) is PublicationSession, "Sessão de publicação obrigatória.")
        self.session = session

    def acquire(self, bundle):
        acquisition_id = uuid.uuid4().hex
        try:
            return self._acquire(bundle, acquisition_id)
        except SkillMutationError as error:
            _LOGGER.warning(
                "skill_acquisition_refused",
                extra={
                    "action": "acquire",
                    "writer": PublicationWriter.QUARANTINE_PROMOTE.value,
                    "phase": "acquisition",
                    "kind": error.kind,
                    "reason": "acquisition_attestation_failed",
                    "acquisition_id": acquisition_id,
                },
            )
            raise

    def _acquire(self, bundle, acquisition_id):
        session = self.session
        session.revalidate()
        context = session.context
        require(context.writer is PublicationWriter.QUARANTINE_PROMOTE)
        snapshot = _bundle_snapshot(bundle)

        def guard():
            session.revalidate()
            require(not session.connection.in_transaction, "I/O fora de transação obrigatório.")

        # O recibo registra aquisição, não instala nem concede autoridade sobre um alvo.
        with publication_errors(), ExitStack() as stack:
            guard()
            try:
                parent = _create_directory(
                    stack, context._fd, acquisition_id, context.root_identity[2]
                )
            except FileExistsError:
                raise SkillMutationError(
                    "conflict", "ID de aquisição ocupado; entrada preservada."
                ) from None
            guard()
            restore_tree(parent, bundle.name, snapshot, guard=guard)
            os.fsync(parent)
            os.fsync(context._fd)
            guard()
            path = context.root / acquisition_id / bundle.name
            proof = replace(capture_source(path), contents=None)
            findings = scan_quarantined(path)
            if findings:
                raise SkillMutationError("denied", "Scan recusou aquisição; entrada preservada.")
            if capture_source(path).entries != proof.entries:
                raise SkillMutationError(
                    "conflict", "Árvore alterada durante o scan; recibo não emitido."
                )
            inventory = publication_inventory_hash(proof)
            require(inventory == publication_inventory_hash(snapshot), "Aquisição alterada.")
            context._track_source(path, proof)
            receipt = AcquisitionReceipt(
                1,
                acquisition_id,
                context.home_id,
                bundle.name,
                "kairos_quarantine_v1",
                context.source_key,
                "unverified",
                inventory,
                acquisition_scan_hash(inventory, findings),
                context.actor,
                proof,
                context.root_identity,
            )
            guard()
            session.publications.issue(receipt, guard=session.revalidate)
            return receipt
