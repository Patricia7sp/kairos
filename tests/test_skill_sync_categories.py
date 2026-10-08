"""Sync reconhece categorias sem perder autoria ou copiar links."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kairos_skills.sync import (
    MANIFEST_NAME,
    NO_BUNDLED_SKILLS_MARKER,
    read_manifest,
    sync_bundled_skills,
    write_manifest,
)


class CategorySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.home = self.root / "home" / "skills"

    def skill(self, path, text="versão fictícia"):
        directory = self.bundle / path
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(text)
        return directory

    def test_categorias_instalam_no_formato_plano_com_anexos(self):
        self.skill("plana")
        source = self.skill("categoria/revisar-docs", "procedimento fictício")
        (source / "references").mkdir()
        (source / "references" / "nota.md").write_text("referência fictícia")
        result = sync_bundled_skills(self.bundle, self.home)
        self.assertEqual(result.copied, ["plana", "revisar-docs"])
        self.assertEqual(
            (self.home / "revisar-docs" / "SKILL.md").read_text(), "procedimento fictício"
        )
        self.assertEqual(
            (self.home / "revisar-docs" / "references" / "nota.md").read_text(),
            "referência fictícia",
        )
        self.assertFalse((self.home / "categoria").exists())

    def test_duplicata_recusa_tudo_sem_mudar_manifesto(self):
        for first in ("igual", "categoria-a/igual"):
            with self.subTest(first=first), TemporaryDirectory() as temporary:
                bundle = Path(temporary)
                for name in (first, "categoria-b/igual", "nova"):
                    directory = bundle / name
                    directory.mkdir(parents=True)
                    (directory / "SKILL.md").write_text("fictício")
                with self.assertRaisesRegex(ValueError, "duplicad"):
                    sync_bundled_skills(bundle, self.home)
                self.assertFalse(self.home.exists())

    def test_links_recusados_antes_de_qualquer_copia(self):
        for kind in ("categoria", "skill", "arquivo", "anexo", "hardlink"):
            with self.subTest(kind=kind), TemporaryDirectory() as temporary:
                bundle = Path(temporary) / "bundle"
                bundle.mkdir()
                normal = bundle / "a-normal"
                normal.mkdir()
                (normal / "SKILL.md").write_text("normal")
                outside = Path(temporary) / "outside"
                outside.mkdir()
                (outside / "SKILL.md").write_text("privado fictício")
                if kind in ("categoria", "skill"):
                    (bundle / "z-link").symlink_to(outside, target_is_directory=True)
                else:
                    bad = bundle / "z-bad"
                    bad.mkdir()
                    if kind == "arquivo":
                        (bad / "SKILL.md").symlink_to(outside / "SKILL.md")
                    else:
                        (bad / "SKILL.md").write_text("normal")
                        if kind == "hardlink":
                            (bad / "ref.md").hardlink_to(outside / "SKILL.md")
                        else:
                            (bad / "references").symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    sync_bundled_skills(bundle, self.home)
                self.assertFalse(self.home.exists())
                self.assertEqual((outside / "SKILL.md").read_text(), "privado fictício")

    def test_skill_local_sem_manifesto_e_preservada(self):
        self.skill("categoria/local", "bundled")
        local = self.home / "local"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text("autoria local")
        result = sync_bundled_skills(self.bundle, self.home)
        self.assertEqual(result.user_modified, ["local"])
        self.assertEqual((local / "SKILL.md").read_text(), "autoria local")
        self.assertNotIn("local", read_manifest(self.home / MANIFEST_NAME))

    def test_categoria_preserva_edicao_exclusao_e_atualiza_intocada(self):
        sources = {
            name: self.skill("categoria/" + name, "v1")
            for name in ("editada", "apagada", "intocada")
        }
        sync_bundled_skills(self.bundle, self.home)
        (self.home / "editada" / "SKILL.md").write_text("minha versão")
        (self.home / "apagada" / "SKILL.md").unlink()
        (self.home / "apagada").rmdir()
        for source in sources.values():
            (source / "SKILL.md").write_text("v2")
        result = sync_bundled_skills(self.bundle, self.home)
        self.assertEqual(result.user_modified, ["editada"])
        self.assertEqual(result.user_deleted, ["apagada"])
        self.assertEqual(result.updated, ["intocada"])
        self.assertEqual((self.home / "editada" / "SKILL.md").read_text(), "minha versão")
        self.assertFalse((self.home / "apagada").exists())
        self.assertEqual((self.home / "intocada" / "SKILL.md").read_text(), "v2")

    def test_categoria_manifesto_v1_preserva_origem_desconhecida(self):
        self.skill("categoria/antiga", "nova versão")
        destination = self.home / "antiga"
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("versão local")
        (self.home / MANIFEST_NAME).write_text("antiga\n")
        result = sync_bundled_skills(self.bundle, self.home)
        self.assertEqual(result.user_modified, ["antiga"])
        self.assertEqual((destination / "SKILL.md").read_text(), "versão local")
        self.assertEqual(read_manifest(self.home / MANIFEST_NAME), {"antiga": ""})

    def test_falha_ao_copiar_atualizacao_preserva_original_e_manifesto(self):
        source = self.skill("plana", "v1")
        sync_bundled_skills(self.bundle, self.home)
        manifest = (self.home / MANIFEST_NAME).read_bytes()
        (source / "SKILL.md").write_text("v2")
        with (
            patch("shutil.copytree", side_effect=OSError("falha fictícia")),
            self.assertRaises(OSError),
        ):
            sync_bundled_skills(self.bundle, self.home)
        self.assertEqual((self.home / "plana" / "SKILL.md").read_text(), "v1")
        self.assertEqual((self.home / MANIFEST_NAME).read_bytes(), manifest)

    def test_falha_no_manifesto_reverte_copias_e_atualizacoes(self):
        source = self.skill("plana", "v1")
        sync_bundled_skills(self.bundle, self.home)
        manifest = (self.home / MANIFEST_NAME).read_bytes()
        (source / "SKILL.md").write_text("v2")
        self.skill("categoria/nova")
        with (
            patch("kairos_skills.sync.write_manifest", side_effect=OSError("falha fictícia")),
            self.assertRaises(OSError),
        ):
            sync_bundled_skills(self.bundle, self.home)
        self.assertEqual((self.home / "plana" / "SKILL.md").read_text(), "v1")
        self.assertFalse((self.home / "nova").exists())
        self.assertEqual((self.home / MANIFEST_NAME).read_bytes(), manifest)

    def test_script_executavel_e_copiado_sem_executar(self):
        source = self.skill("categoria/script")
        script = source / "exec.sh"
        marker = self.root / "exec-marker"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        script.chmod(0o755)
        sync_bundled_skills(self.bundle, self.home)
        installed = self.home / "script" / "exec.sh"
        self.assertEqual(installed.read_bytes(), script.read_bytes())
        self.assertEqual(installed.stat().st_mode & 0o777, 0o755)
        self.assertFalse(marker.exists())

    def test_nome_incompativel_com_manifesto_recusa_sem_alterar_destino(self):
        self.skill("segura", "original")
        sync_bundled_skills(self.bundle, self.home)
        manifest = (self.home / MANIFEST_NAME).read_bytes()
        for name in ("foo:bar", "foo\nbar", "#comentario", " espacada ", "Maiuscula", "a" * 65):
            with self.subTest(name=name), TemporaryDirectory() as temporary:
                bundle = Path(temporary)
                bad = bundle / "categoria" / name
                bad.mkdir(parents=True)
                (bad / "SKILL.md").write_text("conteúdo fictício")
                normal = bundle / "nova"
                normal.mkdir()
                (normal / "SKILL.md").write_text("normal")
                with self.assertRaises(ValueError):
                    sync_bundled_skills(bundle, self.home)
                self.assertFalse((self.home / "nova").exists())
                self.assertEqual((self.home / MANIFEST_NAME).read_bytes(), manifest)
                self.assertEqual((self.home / "segura" / "SKILL.md").read_text(), "original")

    def test_opt_out_nao_varre_bundle_invalido(self):
        self.home.mkdir(parents=True)
        (self.home / NO_BUNDLED_SKILLS_MARKER).touch()
        self.skill("categoria/igual")
        self.skill("outra/igual")
        result = sync_bundled_skills(self.bundle, self.home)
        self.assertTrue(result.skipped_opt_out)
        self.assertEqual(result.copied, [])
        self.assertFalse((self.home / MANIFEST_NAME).exists())

    def test_fifo_no_bundle_e_recusado_sem_esperar_escritor(self):
        import os

        source = self.skill("categoria/fifo")
        os.mkfifo(source / "pipe")
        with self.assertRaises(ValueError):
            sync_bundled_skills(self.bundle, self.home)
        self.assertFalse(self.home.exists())

    def test_link_na_raiz_do_bundle_e_recusado(self):
        self.skill("categoria/segura")
        link = self.root / "bundle-link"
        link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(ValueError):
            sync_bundled_skills(link, self.home)
        self.assertFalse(self.home.exists())

    def test_link_no_destino_nao_recebe_copias(self):
        self.skill("categoria/segura")
        outside = self.root / "outside"
        outside.mkdir()
        self.home.parent.mkdir()
        self.home.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            sync_bundled_skills(self.bundle, self.home)
        self.assertEqual(list(outside.iterdir()), [])

    def test_temporario_preexistente_nao_redireciona_gravacao_manifesto(self):
        self.home.mkdir(parents=True)
        outside = self.root / "outside.txt"
        outside.write_text("privado fictício")
        (self.home / (MANIFEST_NAME + ".tmp")).symlink_to(outside)
        write_manifest(self.home / MANIFEST_NAME, {"segura": "hash-ficticio"})
        self.assertEqual(outside.read_text(), "privado fictício")
        self.assertEqual(read_manifest(self.home / MANIFEST_NAME), {"segura": "hash-ficticio"})
