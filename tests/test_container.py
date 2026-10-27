"""Tarefa 07 — container.

Critério de pronto: "Os 22 RFs atendidos; `kairos login` por `docker exec`
**não** grava `auth.json` como root — o bug de UID de sintoma contraditório."

Os scripts de shell são testados executando-os de verdade, com `id` e
`s6-setuidgid` substituídos por stubs num PATH controlado. Isso exercita a
LÓGICA DE DECISÃO — que é onde os bugs desta unit moram — sem exigir Docker.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import time
import tomllib
import typing
import unittest
from pathlib import Path

from kairos_container import (
    CONTAINER_MODE_FILENAME,
    in_container,
    read_container_mode,
)

REPO = Path(__file__).resolve().parent.parent
DOCKER = REPO / "docker"
DOCKERFILE = REPO / "Dockerfile"


def _runtime_state_for_probe(returncode: int) -> str:
    return "ready" if returncode == 0 else "unavailable"


class ShellHarness(unittest.TestCase):
    """Executa um script com `id` e `s6-setuidgid` falsos."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.bindir = Path(self._tmp.name) / "bin"
        self.bindir.mkdir()
        self.log = Path(self._tmp.name) / "log"

    def tearDown(self):
        self._tmp.cleanup()

    def stub(self, name: str, body: str) -> None:
        p = self.bindir / name
        p.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
        p.chmod(0o755)

    def fake_real_binary(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'#!/bin/bash\necho "REAL uid=$(id -u) args=$*" >> {self.log}\n', encoding="utf-8"
        )
        path.chmod(0o755)

    def run_script(self, script: Path, *args, env=None, uid=0):
        self.stub("id", f'if [ "${{1:-}}" = "-u" ]; then echo {uid}; else /usr/bin/id "$@"; fi')
        e = {**os.environ, "PATH": f"{self.bindir}:{os.environ['PATH']}"}
        e.update(env or {})
        return subprocess.run(
            [str(script), *args], env=e, capture_output=True, text=True, timeout=30, check=False
        )


# ---------------------------------------------------------------------------
class ExecShimTests(ShellHarness):
    """RF-08, RF-09, RF-10, RF-11 — o bug de UID."""

    def setUp(self):
        super().setUp()
        self.shim_src = DOCKER / "bin" / "kairos"
        # O shim aponta para /opt/kairos/.venv/bin/kairos por caminho absoluto.
        # Reescrevemos a raiz para o tmp, preservando a natureza ABSOLUTA do
        # caminho — que é justamente o que o RF-10 exige.
        self.root = Path(self._tmp.name) / "opt"
        self.real = self.root / "kairos/.venv/bin/kairos"
        self.fake_real_binary(self.real)
        self.shim = Path(self._tmp.name) / "shim.sh"
        self.shim.write_text(
            self.shim_src.read_text(encoding="utf-8").replace(
                'REAL="/opt/kairos/.venv/bin/kairos"', f'REAL="{self.real}"'
            ),
            encoding="utf-8",
        )
        self.shim.chmod(0o755)

    def _setuidgid_stub(self):
        self.stub("s6-setuidgid", f'echo "SETUIDGID user=$1" >> {self.log}\nshift\nexec "$@"')

    def test_rf09_nao_root_vai_direto_ao_binario_sem_troca_de_privilegio(self):
        self._setuidgid_stub()
        r = self.run_script(self.shim, "chat", uid=10000)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.log.read_text()
        self.assertIn("REAL", log)
        self.assertNotIn("SETUIDGID", log, "não-root não pode trocar privilégio")

    def test_rf08_root_DERRUBA_privilegio_antes_de_executar(self):
        """O bug: `docker exec <c> kairos login` como root gravava root-owned."""
        self._setuidgid_stub()
        r = self.run_script(self.shim, "login", uid=0)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.log.read_text()
        self.assertIn("SETUIDGID user=kairos", log)
        self.assertIn("args=login", log)

    def test_rf11_opt_out_mantem_root(self):
        self._setuidgid_stub()
        for value in ("1", "true", "TRUE", "yes", "Yes"):
            with self.subTest(value=value):
                self.log.unlink(missing_ok=True)
                r = self.run_script(
                    self.shim, "doctor", uid=0, env={"KAIROS_DOCKER_EXEC_AS_ROOT": value}
                )
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("SETUIDGID", self.log.read_text())

    def test_valor_nao_reconhecido_do_opt_out_NAO_mantem_root(self):
        # O default é o caminho seguro: só 1/true/yes optam por sair.
        self._setuidgid_stub()
        r = self.run_script(
            self.shim, "doctor", uid=0, env={"KAIROS_DOCKER_EXEC_AS_ROOT": "talvez"}
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SETUIDGID", self.log.read_text())

    def test_sem_mecanismo_de_queda_o_shim_RECUSA(self):
        """O comportamento mudou na verificação com a imagem real.

        Antes: avisar e rodar como root. Isso recria o bug que o shim existe
        para prevenir, e o recria em silêncio prático — ninguém lê o stderr de
        um `docker exec ... kairos login`, e o sintoma só aparece uma hora
        depois como falha de autenticação contraditória.

        Agora: recusa com exit 77 e aponta o opt-out.

        Este harness roda no HOST, que pode ter `/usr/bin/setpriv` — e o shim
        o usa por caminho absoluto, de propósito. Então aqui só dá para
        afirmar a propriedade mais fraca, porém a que importa: **sem mecanismo
        de queda, o binário real NÃO é executado**. O exit 77 e a mensagem são
        verificados contra a imagem real, em `RealImageTests`.
        """
        r = self.run_script(self.shim, "chat", uid=0)
        self.assertNotEqual(r.returncode, 0, "não pode ter sucesso como root")
        registrado = self.log.read_text() if self.log.exists() else ""
        self.assertNotIn("REAL", registrado, "recusar significa NÃO executar")

    def test_rf10_o_caminho_e_absoluto_logo_imune_a_PATH(self):
        fonte = self.shim_src.read_text(encoding="utf-8")
        self.assertIn('REAL="/opt/kairos/.venv/bin/kairos"', fonte)
        # Nenhuma sentinela é necessária, e não deve haver nenhuma.
        self.assertNotIn("SHIM_SENTINEL", fonte)

    def test_rf10_recursao_e_impossivel_mesmo_com_PATH_hostil(self):
        # PATH aponta para o próprio shim como se fosse o binário: o caminho
        # absoluto impede a reentrada.
        self._setuidgid_stub()
        (self.bindir / "kairos").write_text(
            "#!/bin/bash\necho RECURSAO >&2\nexit 99\n", encoding="utf-8"
        )
        (self.bindir / "kairos").chmod(0o755)
        r = self.run_script(self.shim, "chat", uid=0)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("RECURSAO", r.stderr)


# ---------------------------------------------------------------------------
class DispatchTests(ShellHarness):
    """RF-01, RF-02, RF-03."""

    def setUp(self):
        super().setUp()
        self.src = (DOCKER / "entrypoint-dispatch.sh").read_text(encoding="utf-8")

    def test_rf01_decide_por_PID_1(self):
        self.assertIn('if [ "$$" -eq 1 ]; then', self.src)
        self.assertIn("exec /init", self.src)

    def test_rf02_o_fallback_ANUNCIA_a_degradacao(self):
        # Falhar em silêncio seria pior; seguir em silêncio também.
        self.assertIn(">&2", self.src)
        self.assertIn("INDISPON", self.src)

    def test_rf03_o_fallback_rehidrata_o_PATH_do_s6(self):
        code = code_only(DOCKER / "entrypoint-dispatch.sh")
        self.assertIn("/command", code)
        self.assertIn("/package/admin/s6/command", code)

    def test_o_fallback_roda_o_bootstrap_e_DEPOIS_o_wrapper(self):
        pos_stage2 = self.src.index("stage2-hook.sh")
        pos_wrapper = self.src.rindex("main-wrapper.sh")
        self.assertLess(
            pos_stage2, pos_wrapper, "o bootstrap precisa preceder a execução do comando"
        )

    def test_o_caminho_nao_pid1_executa_de_fato(self):
        """Comportamento, não texto: degradar é seguir rodando."""
        stage2 = Path(self._tmp.name) / "stage2.sh"
        wrapper = Path(self._tmp.name) / "wrapper.sh"
        for p, tag in ((stage2, "STAGE2"), (wrapper, "WRAPPER")):
            p.write_text(f'#!/bin/bash\necho "{tag} $*" >> {self.log}\n', encoding="utf-8")
            p.chmod(0o755)
        script = Path(self._tmp.name) / "dispatch.sh"
        script.write_text(
            self.src.replace("/opt/kairos/docker/stage2-hook.sh", str(stage2)).replace(
                "/opt/kairos/docker/main-wrapper.sh", str(wrapper)
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)
        r = self.run_script(script, "chat", "-q", "ping")
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.log.read_text()
        self.assertIn("STAGE2", log)
        self.assertIn("WRAPPER chat -q ping", log)


# ---------------------------------------------------------------------------
def code_only(path: Path) -> str:
    """O script sem comentários.

    Grep no texto cru casa com a explicação de por que algo *não* é feito, e
    então o teste afirma o contrário do que pretende. Aqui só o executável
    conta.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


class BootstrapTests(ShellHarness):
    """RF-04, RF-05, RF-16."""

    def test_rf05_o_stage2_NAO_derruba_privilegio(self):
        # A queda é por serviço (no `run` de cada um) e no main-wrapper.
        # Derrubar aqui tiraria do stage2 o root de que ele precisa para
        # usermod/groupmod/chown.
        self.assertNotIn("s6-setuidgid", code_only(DOCKER / "stage2-hook.sh"))

    def test_o_stage2_NAO_executa_o_CMD(self):
        # Scripts de cont-init.d rodam sem argumentos: os args do usuário nem
        # chegam aqui. Um `exec "$@"` seria contrato quebrado silenciosamente.
        code = code_only(DOCKER / "stage2-hook.sh")
        self.assertNotIn('exec "$@"', code)
        self.assertNotIn("exec $@", code)

    def test_rf04_as_quatro_responsabilidades_estao_la(self):
        src = code_only(DOCKER / "stage2-hook.sh")
        for marca in ("usermod", "chown", "config.yaml", "skills"):
            with self.subTest(marca=marca):
                self.assertIn(marca, src)

    def test_rf16_o_marcador_e_escrito_com_o_formato_do_contrato(self):
        src = code_only(DOCKER / "stage2-hook.sh")
        self.assertIn(".container-mode", src)
        for chave in ("runtime=", "uid=", "gid=", "home=", "supervised="):
            with self.subTest(chave=chave):
                self.assertIn(chave, src)


# ---------------------------------------------------------------------------
class ContInitOrderTests(unittest.TestCase):
    """RF-13 — a ordem lexicográfica é o mecanismo, não um detalhe."""

    def test_a_ordem_lexicografica_produz_a_sequencia_correta(self):
        nomes = sorted(p.name for p in (DOCKER / "cont-init.d").iterdir())
        self.assertEqual(
            nomes,
            ["01-kairos-setup", "015-supervise-perms", "02-reconcile-profiles"],
        )

    def test_o_setup_precede_as_perms_que_precedem_a_reconciliacao(self):
        # 015 precisa do UID já remapeado pelo 01; 02 precisa do $KAIROS_HOME
        # já semeado. "015" entre "01" e "02" é intencional, não erro de
        # digitação.
        nomes = sorted(p.name for p in (DOCKER / "cont-init.d").iterdir())
        self.assertLess(nomes.index("01-kairos-setup"), nomes.index("015-supervise-perms"))
        self.assertLess(nomes.index("015-supervise-perms"), nomes.index("02-reconcile-profiles"))

    def test_todos_sao_executaveis(self):
        for p in (DOCKER / "cont-init.d").iterdir():
            with self.subTest(script=p.name):
                self.assertTrue(p.stat().st_mode & stat.S_IXUSR, f"{p.name} não é executável")

    def test_todos_tem_sintaxe_valida(self):
        for p in [
            *(DOCKER / "cont-init.d").iterdir(),
            DOCKER / "stage2-hook.sh",
            DOCKER / "main-wrapper.sh",
            DOCKER / "entrypoint-dispatch.sh",
            DOCKER / "entrypoint.sh",
            DOCKER / "bin" / "kairos",
        ]:
            with self.subTest(script=p.name):
                r = subprocess.run(
                    ["bash", "-n", str(p)], capture_output=True, text=True, check=False
                )
                self.assertEqual(r.returncode, 0, r.stderr)


# ---------------------------------------------------------------------------
class ServicesTests(unittest.TestCase):
    """RF-12, RF-14, RF-19."""

    def test_rf14_servicos_distintos_dependentes_de_base(self):
        for svc in ("main-kairos", "dashboard", "runtime"):
            with self.subTest(svc=svc):
                d = DOCKER / "s6-rc.d" / svc
                self.assertEqual((d / "type").read_text().strip(), "longrun")
                self.assertTrue((d / "dependencies.d" / "base").exists())
                self.assertTrue((d / "run").exists())

    def test_o_bundle_user_contem_todos_os_servicos(self):
        contents = {p.name for p in (DOCKER / "s6-rc.d/user/contents.d").iterdir()}
        self.assertEqual(contents, {"main-kairos", "dashboard", "runtime"})

    def test_rf19_cada_servico_derruba_o_proprio_privilegio(self):
        for svc in ("main-kairos", "dashboard", "runtime"):
            with self.subTest(svc=svc):
                self.assertIn("s6-setuidgid", code_only(DOCKER / "s6-rc.d" / svc / "run"))

    def test_o_dashboard_caindo_nao_derruba_o_container(self):
        # O gateway define a vida do container; o dashboard é acessório.
        finish = (DOCKER / "s6-rc.d/dashboard/finish").read_text()
        self.assertIn("exit 0", finish)
        self.assertFalse((DOCKER / "s6-rc.d/main-kairos/finish").exists())

    def test_rf12_as_perms_de_supervisao_alcancam_o_usuario(self):
        code = code_only(DOCKER / "cont-init.d/015-supervise-perms")
        self.assertIn("supervise", code)
        self.assertIn("chown", code)


# ---------------------------------------------------------------------------
class DockerfileTests(unittest.TestCase):
    """RF-18, RF-20, RF-21, RF-22."""

    def setUp(self):
        self.src = DOCKERFILE.read_text(encoding="utf-8")

    def test_rf20_o_shim_precede_a_venv_no_PATH(self):
        m = re.search(r'PATH="([^"]+)"', self.src)
        self.assertIsNotNone(m)
        path = m.group(1)
        # Inverter esta ordem reintroduz o bug de UID.
        self.assertLess(path.index("/opt/kairos/bin"), path.index("/opt/kairos/.venv/bin"))

    def test_rf18_o_UID_e_parametrizavel_e_o_default_e_10000(self):
        self.assertIn("ARG KAIROS_UID=10000", self.src)
        # Propriedade, não ordem de flags: fixar a string exata fez este teste
        # quebrar ao acrescentarmos `-l`, que é melhoria e não regressão.
        m = re.search(r"^RUN useradd (.+)$", self.src, re.MULTILINE)
        self.assertIsNotNone(m)
        flags = m.group(1)
        self.assertIn("-u ${KAIROS_UID}", flags)

    def test_useradd_usa_l_por_causa_do_UID_alto(self):
        """Sem `-l`, um UID alto gera lastlog/faillog esparsos de centenas de
        MB na imagem (hadolint DL3046)."""
        m = re.search(r"^RUN useradd (.+)$", self.src, re.MULTILINE)
        self.assertIn("-l", m.group(1).split())

    def test_o_entrypoint_e_o_dispatcher(self):
        self.assertIn('ENTRYPOINT ["/opt/kairos/docker/entrypoint-dispatch.sh"]', self.src)

    def test_o_volume_e_o_home_do_usuario(self):
        self.assertIn('VOLUME ["/opt/data"]', self.src)
        self.assertIn("-d /opt/data kairos", self.src)

    def test_a_extensao_cjk_falhar_nao_derruba_o_build(self):
        self.assertIn("build.sh /opt/kairos/lib ||", self.src)

    def test_codex_pinned_preserva_o_pacote_verificado_amd64(self):
        self.assertIn("ARG CODEX_VERSION=0.154.0", self.src)
        self.assertIn(
            "fc6e3e3b85f2cf7d664520ee5c66a7fe4aa12bae7d46834f47e2f165fd0d6f78",
            self.src,
        )
        self.assertIn("codex-package-x86_64-unknown-linux-musl.tar.gz", self.src)
        self.assertIn("sha256sum -c -", self.src)
        self.assertIn("/usr/local/lib/codex", self.src)

    def test_a_imagem_copia_o_pacote_runtime(self):
        self.assertIn("COPY kairos_runtime/ ./kairos_runtime/", self.src)

    def test_estado_runtime_esperado_segue_o_retorno_do_probe_real(self):
        self.assertEqual(_runtime_state_for_probe(0), "ready")
        self.assertEqual(_runtime_state_for_probe(1), "unavailable")

    def test_distribuicao_descobre_subpacotes_e_inclui_spa(self):
        config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        setuptools = config["tool"]["setuptools"]

        self.assertEqual(setuptools["packages"]["find"]["include"], ["kairos*"])
        self.assertIn("ui/**/*", setuptools["package-data"]["kairos_web"])

    def test_docker_build_check_nao_reporta_warning(self):
        r = subprocess.run(
            ["docker", "build", "--check", str(REPO)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertIn("no warnings found", r.stdout + r.stderr)


# ---------------------------------------------------------------------------
class ShellCheckTests(unittest.TestCase):
    """Lint de shell. Pulado quando a ferramenta não está instalada — como a
    extensão CJK, é dependência de desenvolvimento, não de execução."""

    SCRIPTS: typing.ClassVar[list] = [
        DOCKER / "runtime-service.sh",
        DOCKER / "entrypoint-dispatch.sh",
        DOCKER / "stage2-hook.sh",
        DOCKER / "main-wrapper.sh",
        DOCKER / "entrypoint.sh",
        DOCKER / "bin" / "kairos",
        DOCKER / "cont-init.d" / "01-kairos-setup",
        DOCKER / "cont-init.d" / "015-supervise-perms",
        DOCKER / "cont-init.d" / "02-reconcile-profiles",
        REPO / "native" / "fts5_cjk" / "build.sh",
    ]

    def test_todos_os_scripts_passam_no_shellcheck(self):
        import shutil

        sc = shutil.which("shellcheck")
        if sc is None:
            self.skipTest("shellcheck não instalado")
        for script in self.SCRIPTS:
            with self.subTest(script=script.name):
                r = subprocess.run([sc, str(script)], capture_output=True, text=True, check=False)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class DeprecatedShimTests(unittest.TestCase):
    """RF-15."""

    def test_avisa_que_NAO_executa_o_CMD(self):
        src = (DOCKER / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("DEPRECIA", src.upper())
        self.assertIn("NÃO executa", src)
        self.assertIn("stage2-hook.sh", src)


# ---------------------------------------------------------------------------
class ContainerModeTests(unittest.TestCase):
    """RF-16 — o lado consumidor do contrato."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = os.environ.get("KAIROS_HOME")
        os.environ["KAIROS_HOME"] = self._tmp.name
        self.marker = Path(self._tmp.name) / CONTAINER_MODE_FILENAME

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = self._saved
        self._tmp.cleanup()

    def write(self, text):
        self.marker.write_text(text, encoding="utf-8")

    def test_fast_path_e_so_um_stat(self):
        self.assertFalse(in_container())
        self.write("runtime=s6\n")
        self.assertTrue(in_container())

    def test_fora_de_container_o_parse_devolve_None(self):
        self.assertIsNone(read_container_mode())

    def test_parse_autoritativo(self):
        self.write("runtime=s6\nuid=10000\ngid=10000\nhome=/opt/data\nsupervised=1\n")
        m = read_container_mode()
        self.assertEqual((m.runtime, m.uid, m.gid, str(m.home)), ("s6", 10000, 10000, "/opt/data"))
        self.assertTrue(m.supervised)
        self.assertFalse(m.degraded)

    def test_o_caminho_nao_supervisionado_e_visivel(self):
        # É a resposta para "por que o dashboard não responde?" — melhor que
        # um timeout.
        self.write("runtime=s6\nsupervised=0\n")
        self.assertTrue(read_container_mode().degraded)

    def test_supervised_ausente_conta_como_supervisionado(self):
        # Assumir degradação faria o CLI avisar sobre um problema inexistente.
        self.write("runtime=s6\n")
        self.assertFalse(read_container_mode().degraded)

    def test_linha_malformada_nao_levanta(self):
        self.write("runtime=s6\nlixo sem igual\n\n# comentário\nuid=nao-numero\n")
        m = read_container_mode()
        self.assertEqual(m.runtime, "s6")
        self.assertEqual(m.uid, 10000)  # default

    def test_chave_desconhecida_e_preservada_nao_descartada(self):
        # Uma chave nova escrita por um bootstrap mais recente não pode
        # sumir só porque este CLI é mais antigo.
        self.write("runtime=s6\nfuturo=algo\n")
        self.assertEqual(read_container_mode().unknown, {"futuro": "algo"})

    def test_arquivo_vazio_nao_levanta(self):
        self.write("")
        self.assertIsNotNone(read_container_mode())


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
class RealImageTests(unittest.TestCase):
    """Verificação contra a imagem construída de verdade.

    `docker build --check` valida a ESTRUTURA do Dockerfile e não pega deriva
    entre listas, diretório de destino ausente, nem PATH de runtime. Os três
    bugs mais graves da Tarefa 07 só apareceram aqui.

    Pulado quando Docker não está disponível — é verificação de integração,
    não requisito para rodar a suíte.
    """

    IMAGE = "kairos:test"
    STUB = "kairos:stub"

    @classmethod
    def setUpClass(cls):
        import shutil

        if shutil.which("docker") is None:
            raise unittest.SkipTest("docker não disponível")
        r = subprocess.run(
            ["docker", "image", "inspect", cls.IMAGE], capture_output=True, text=True, check=False
        )
        if r.returncode != 0:
            raise unittest.SkipTest(
                f"imagem {cls.IMAGE} não construída — rode: docker build -t {cls.IMAGE} ."
            )

    def run_in(
        self, *cmd, image=None, entrypoint="/bin/bash", extra=(), input_text=None, timeout=120
    ):
        args = ["docker", "run", "--rm", *extra]
        if input_text is not None:
            args.append("--interactive")
        if entrypoint:
            args += ["--entrypoint", entrypoint]
        args += [image or self.IMAGE, *cmd]
        return subprocess.run(
            args, input=input_text, capture_output=True, text=True, timeout=timeout, check=False
        )

    # -- estrutura ---------------------------------------------------------

    def test_learn_creation_confirmation_and_rollback_in_real_image(self):
        from pathlib import Path

        script = Path(__file__).parent.resolve() / "skill_learn_image_acceptance.py"
        result = self.run_in(
            "/acceptance/skill_learn_image_acceptance.py",
            entrypoint="/opt/kairos/.venv/bin/python",
            extra=(
                "--user",
                "10000:10000",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,mode=1777",
                "--mount",
                f"type=bind,src={script},dst=/acceptance/skill_learn_image_acceptance.py,readonly",
            ),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["creation"], "committed")
        self.assertEqual(report["rollback"], "committed")
        self.assertEqual(report["origin"], "user")

    def _publication_acceptance(self, mode):
        from pathlib import Path

        tests = Path(__file__).resolve().parent
        result = self.run_in(
            "/acceptance/skill_publication_image_acceptance.py",
            mode,
            entrypoint="/opt/kairos/.venv/bin/python",
            timeout=300,
            extra=(
                "--user",
                "10000:10000",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,mode=1777",
                "--tmpfs",
                "/acceptance-home:rw,nosuid,nodev,uid=10000,gid=10000,mode=700,size=128m",
                "--tmpfs",
                "/acceptance-quarantine:rw,nosuid,nodev,uid=10000,gid=10000,mode=700,size=64m",
                "--mount",
                f"type=bind,src={tests / 'skill_publication_image_acceptance.py'},dst=/acceptance/skill_publication_image_acceptance.py,readonly",
                "--mount",
                f"type=bind,src={tests / 'fixtures/skill_publication_process.py'},dst=/acceptance/fixtures/skill_publication_process.py,readonly",
            ),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_cross_device_quarantine_in_real_image(self):
        report = self._publication_acceptance("cross-device")
        self.assertNotEqual(report["home_device"], report["quarantine_device"])
        self.assertEqual(report["sigkills"], 4)

    def test_skill_publication_recovery_and_backup(self):
        report = self._publication_acceptance("full")
        self.assertGreater(report["sigkills"], 40)
        self.assertEqual(report["backup"], "complete")
        self.assertFalse(report["authority_transferred"])

    def test_o_shim_resolve_antes_da_venv_no_PATH_real(self):
        r = self.run_in("-c", "command -v kairos")
        self.assertEqual(r.stdout.strip(), "/opt/kairos/bin/kairos")

    def test_o_usuario_existe_com_uid_10000(self):
        r = self.run_in("-c", "id -u kairos")
        self.assertEqual(r.stdout.strip(), "10000")

    def test_a_ordem_de_cont_init_no_disco_da_imagem(self):
        r = self.run_in("-c", "ls /etc/cont-init.d/")
        self.assertEqual(
            r.stdout.split(), ["01-kairos-setup", "015-supervise-perms", "02-reconcile-profiles"]
        )

    def test_s6_setuidgid_existe_no_caminho_absoluto_que_o_shim_usa(self):
        # O bug: o shim dependia do PATH, e num `docker exec` cru /command não
        # está lá — quem o semeia é o /init, que não roda nesse caminho.
        r = self.run_in("-c", "test -x /command/s6-setuidgid && echo ok")
        self.assertEqual(r.stdout.strip(), "ok")

    def test_todo_pacote_declarado_no_pyproject_esta_na_imagem(self):
        """Todo pacote raiz descoberto pelo setuptools entra na imagem.

        O `pyproject.toml` descobre `kairos*`; o Dockerfile ainda copia as
        raízes uma a uma. Adicionar uma raiz e esquecer o COPY quebra o build.
        """
        import re as _re

        config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            config["tool"]["setuptools"]["packages"]["find"]["include"],
            ["kairos*"],
        )
        package_roots = sorted(
            path.name
            for path in REPO.glob("kairos*")
            if path.is_dir() and (path / "__init__.py").is_file()
        )
        self.assertTrue(package_roots)
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        copied_roots = _re.findall(r"^COPY ([^ /]+)/", dockerfile, flags=_re.MULTILINE)
        for package_root in package_roots:
            with self.subTest(package=package_root):
                self.assertIn(package_root, copied_roots)

    def test_web_and_operational_journal_load_in_the_real_image(self):
        result = self.run_in(
            "-c",
            "from pathlib import Path; import tempfile; "
            "from kairos_web.server import app; "
            "from kairos_observability import record_service_event, read_service_events; "
            "home = Path(tempfile.mkdtemp()); "
            "assert record_service_event(home, 'web.started'); "
            "assert read_service_events(home)['events'][0]['code'] == 'web.started'; "
            "assert '/api/logs' in app.openapi()['paths']",
            entrypoint="/opt/kairos/.venv/bin/python",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_filesystem_capture_in_the_real_image(self):
        result = self.run_in(
            "-c",
            "import os, tempfile, hashlib; from pathlib import Path; "
            "from kairos_filesystem.contract import TreeLimits; "
            "from kairos_filesystem.tree import capture_tree, restore_tree, delete_verified_tree; "
            "home = Path(tempfile.mkdtemp()); (home / 'tree').mkdir(); "
            "(home / 'tree/file').write_bytes(b'bin\\x00'); "
            "fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY); "
            "snapshot = capture_tree(fd, 'tree', limits=TreeLimits(10, 10, 2, 1), include_contents=True); "
            "assert snapshot.contents == {'file': b'bin\\x00'}; "
            "assert snapshot.entries[1].sha256 == hashlib.sha256(b'bin\\x00').hexdigest(); "
            "restored = restore_tree(fd, 'copy', snapshot); "
            "assert restored.contents == snapshot.contents; "
            "delete_verified_tree(fd, 'copy', restored); "
            "assert not (home / 'copy').exists(); os.close(fd)",
            entrypoint="/opt/kairos/.venv/bin/python",
            extra=("--tmpfs", "/tmp"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_skill_adoption_cli_lineage_and_A_B_comparison_in_real_image(self):
        script = r"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

secret = 'real-image-private-sentinel'
binary = b'\x00\xff\x80adoption-image'

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    home = root / 'home'
    skill = home / 'skills' / 'legacy-image'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(secret, encoding='utf-8')
    asset = skill / 'references' / 'private.bin'
    asset.parent.mkdir()
    asset.write_bytes(binary)
    asset.chmod(0o641)
    env = {**os.environ, 'KAIROS_HOME': str(home)}

    def cli(*args):
        result = subprocess.run(
            ['kairos', *args], capture_output=True, text=True, env=env,
            timeout=30, check=False,
        )
        assert secret not in result.stdout + result.stderr
        assert str(root) not in result.stdout + result.stderr
        return result

    adopted = cli('skills', 'adopt', 'legacy-image', '--yes', '--json')
    assert adopted.returncode == 0, adopted.stderr
    adoption_a = json.loads(adopted.stdout)
    assert adoption_a['state'] == 'committed'
    assert adoption_a['adoption_active'] is True

    removed = cli('skills', 'remove', 'legacy-image', '--yes', '--json')
    assert removed.returncode == 0, removed.stderr
    removal = json.loads(removed.stdout)
    restored = cli('skills', 'rollback', removal['operation_id'], '--json')
    assert restored.returncode == 0, restored.stderr
    restore = json.loads(restored.stdout)
    assert restore['state'] == 'committed'
    assert (skill / 'references' / 'private.bin').read_bytes() == binary
    assert (skill / 'references' / 'private.bin').stat().st_mode & 0o777 == 0o641

    revoked = cli(
        'skills', 'unadopt', 'legacy-image', '--yes',
        '--expect-adoption', adoption_a['adoption_id'], '--json',
    )
    assert revoked.returncode == 0, revoked.stderr
    assert json.loads(revoked.stdout)['adoption_active'] is False

    denied_remove = cli('skills', 'remove', 'legacy-image', '--yes', '--json')
    assert denied_remove.returncode == 77
    assert denied_remove.stdout == ''
    assert (skill / 'SKILL.md').read_text(encoding='utf-8') == secret
    assert (skill / 'references' / 'private.bin').read_bytes() == binary

    adopted_b_result = cli('skills', 'adopt', 'legacy-image', '--yes', '--json')
    assert adopted_b_result.returncode == 0, adopted_b_result.stderr
    adoption_b = json.loads(adopted_b_result.stdout)
    assert adoption_b['adoption_id'] != adoption_a['adoption_id']
    compare = cli(
        'skills', 'unadopt', 'legacy-image', '--yes',
        '--expect-adoption', adoption_a['adoption_id'], '--json',
    )
    assert compare.returncode == 1
    assert compare.stdout == ''
    identified = cli('skills', 'identify', 'legacy-image', '--json')
    assert identified.returncode == 0, identified.stderr
    report = json.loads(identified.stdout)
    assert report['adoption_id'] == adoption_b['adoption_id']
    assert report['adoption_active'] is True

    history = cli('skills', 'history', '--json')
    assert history.returncode == 0, history.stderr
    actions = [event['action'] for event in json.loads(history.stdout)]
    assert actions == ['adopt', 'unadopt', 'restore', 'remove', 'adopt']
"""
        result = self.run_in(
            "-",
            input_text=script,
            entrypoint="/opt/kairos/.venv/bin/python",
            extra=("--tmpfs", "/tmp", "--user", "10000:10000"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sigkill_adoption_and_restore_boundaries_in_real_image(self):
        script = r"""
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
secret = 'real-image-recovery-sentinel'
worker = "from kairos_skills.org_policy import observe_org_policy\nfrom kairos_skills.org_policy_contract import OrgPolicyContext\nimport sys\nfrom pathlib import Path\nfrom unittest.mock import patch\nfrom kairos_domain.ownership import Actor\nfrom kairos_skills.adoptions import SkillAdoptionService\nfrom kairos_skills.catalog import catalog_home_id\nfrom kairos_skills.mutations import SkillMutationService\nfrom kairos_skills.removal_io import SkillRemovalFiles\nfrom kairos_state import connect\nfrom kairos_state.migrations import migrate\nfrom kairos_state.repositories.skill_mutations import SkillMutationRepository\nfrom kairos_state.repositories.skill_adoptions import SkillAdoptionRepository\nmode, raw_home, name, *extra = sys.argv[1:]\nhome = Path(raw_home)\nwith observe_org_policy(OrgPolicyContext('none', None), home) as policy:\n    database = connect(home / 'state.db')\n    migrate(database)\n    repository = SkillMutationRepository(database, home_id=catalog_home_id(home))\n    if mode == 'adopt':\n        original = SkillAdoptionRepository.prepare_adopt\n\n        def stop_after_prepare(repo, draft, snapshot):\n            result = original(repo, draft, snapshot)\n            print('BOUNDARY:' + draft.operation_id, flush=True)\n            input()\n            return result\n        with patch.object(SkillAdoptionRepository, 'prepare_adopt', stop_after_prepare):\n            SkillAdoptionService(home, repository, policy=policy).adopt(name, actor=Actor.USER_FOREGROUND, confirmed=True)\n    else:\n        original = SkillRemovalFiles.publish\n\n        def stop_after_publish(files, operation_id, skill_name):\n            original(files, operation_id, skill_name)\n            print('BOUNDARY:' + operation_id, flush=True)\n            input()\n        service = SkillMutationService(home, repository, policy=policy)\n        with patch.object(SkillRemovalFiles, 'publish', stop_after_publish):\n            service.rollback(extra[0], actor=Actor.USER_FOREGROUND)\n"

def cli(home, *args):
    env = {**os.environ, 'KAIROS_HOME': str(home)}
    result = subprocess.run(['kairos', *args], capture_output=True, text=True, env=env, timeout=30, check=False)
    assert secret not in result.stdout + result.stderr
    return result

def interrupt(home, mode, name, *extra):
    command = ['/opt/kairos/.venv/bin/python', '-c', worker, mode, str(home), name, *extra]
    env = {**os.environ, 'KAIROS_HOME': str(home)}
    process = subprocess.Popen(command, cwd='/opt/kairos', env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([process.stdout], [], [], 20)
        assert ready, 'subprocesso não chegou à fronteira durável'
        line = process.stdout.readline().strip()
        assert line.startswith('BOUNDARY:'), line
        os.kill(process.pid, signal.SIGKILL)
        assert process.wait(timeout=5) == -signal.SIGKILL
        return line.split(':', 1)[1]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()

def reconcile(home, name):
    with observe_org_policy(OrgPolicyContext('none', None), home) as policy:
        from kairos_skills.mutation_lock import skill_mutation_lock
        from kairos_skills.mutation_recovery import reconcile_pending
        from kairos_skills.removal_io import SkillRemovalFiles
        from kairos_skills.catalog import catalog_home_id
        from kairos_state import connect
        from kairos_state.repositories.skill_mutations import SkillMutationRepository
        database = connect(home / 'state.db')
        try:
            repository = SkillMutationRepository(database, home_id=catalog_home_id(home))
            with skill_mutation_lock(home), SkillRemovalFiles(home) as files:
                return reconcile_pending(repository, files, name=name, policy=policy)
        finally:
            database.close()
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    home = root / 'home'
    skill = home / 'skills' / 'legacy-recovery'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(secret, encoding='utf-8')
    (skill / 'run.bin').write_bytes(b'\x00\xff')
    adoption_id = interrupt(home, 'adopt', 'legacy-recovery')
    assert reconcile(home, 'legacy-recovery')
    identified = cli(home, 'skills', 'identify', 'legacy-recovery', '--json')
    assert identified.returncode == 0, identified.stderr
    assert json.loads(identified.stdout)['adoption_id'] == adoption_id
    assert json.loads(identified.stdout)['adoption_active'] is True
    removed = cli(home, 'skills', 'remove', 'legacy-recovery', '--yes', '--json')
    assert removed.returncode == 0, removed.stderr
    removal_id = json.loads(removed.stdout)['operation_id']
    restore_id = interrupt(home, 'restore', 'legacy-recovery', removal_id)
    assert reconcile(home, 'legacy-recovery')
    history = cli(home, 'skills', 'history', '--json')
    assert history.returncode == 0, history.stderr
    events = json.loads(history.stdout)
    restored = next((event for event in events if event['id'] == restore_id))
    assert restored['action'] == 'restore'
    assert restored['state'] == 'committed'
    identified = cli(home, 'skills', 'identify', 'legacy-recovery', '--json')
    assert identified.returncode == 0, identified.stderr
    report = json.loads(identified.stdout)
    assert report['adoption_id'] == adoption_id
    assert report['adoption_active'] is True
    assert (skill / 'SKILL.md').read_text(encoding='utf-8') == secret
    assert (skill / 'run.bin').read_bytes() == b'\x00\xff'
"""
        result = self.run_in(
            "-",
            input_text=script,
            entrypoint="/opt/kairos/.venv/bin/python",
            extra=("--tmpfs", "/tmp", "--user", "10000:10000"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_identify_reads_tree_in_real_image_without_mutation(self):
        script = r"""
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
import shutil
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch
from kairos_domain.ownership import Actor
from kairos_skills import identification
from kairos_skills.catalog import catalog_home_id
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.mutations import SkillMutationService
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository

def inventory(home):
    result = {}
    for path in (home, *sorted(home.rglob('*'))):
        info = path.lstat()
        result[str(path.relative_to(home))] = (info.st_mode, info.st_dev, info.st_ino, info.st_nlink, path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
    return result

def observe(home, name):
    with observe_org_policy(OrgPolicyContext('none', None), home) as policy:
        before = inventory(home)
        report = identify_skill(home, name, policy=policy)
        assert inventory(home) == before
        assert report.known_protection == ()
        assert report.protection_coverage == 'namespace_and_recorded_only'
        return report
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    home = root / 'home'
    with observe_org_policy(OrgPolicyContext('none', None), home) as policy:
        home.mkdir()
        source = root / 'source.md'
        source.write_text("---\nname: user-fixture\ndescription: Ficticia.\nversion: '0.1.0'\n---\nProcedimento ficticio.\n")
        db = connect(home / 'state.db')
        try:
            migrate(db)
            repo = SkillMutationRepository(db, home_id=catalog_home_id(home))
            service = SkillMutationService(home, repo, policy=policy)
            created = service.add(source, actor=Actor.USER_FOREGROUND)
            report = observe(home, 'user-fixture')
            assert report.historical_creation_origin == 'user'
            assert report.installation_source == 'local'
            assert report.authorization_basis == 'proven_write'
            assert report.identity_valid is True and report.removivel
            assert report.current_installation_id == created.operation_id
            assert report.historical_creation_id == created.operation_id
            legacy = home / 'skills' / 'legacy-fixture'
            legacy.mkdir()
            (legacy / 'SKILL.md').write_bytes(b'# Legado sem semver\n')
            asset = legacy / 'run.bin'
            asset.write_bytes(b'\x00\xff\x01')
            asset.chmod(489)
            (legacy / 'empty').mkdir(mode=456)
            report = observe(home, 'legacy-fixture')
            assert report.historical_creation_origin == 'unknown'
            assert report.installation_source == 'unknown'
            assert report.authorization_basis == 'none'
            assert report.ownership == 'user'
            assert report.identity_valid is None and (not report.removivel)
            assert asset.read_bytes() == b'\x00\xff\x01'
            assert stat.S_IMODE(asset.stat().st_mode) == 489
            assert not list((legacy / 'empty').iterdir())
            target = home / 'skills' / 'user-fixture'
            target.rename(root / 'previous')
            shutil.copytree(root / 'previous', target)
            report = observe(home, 'user-fixture')
            assert report.historical_creation_origin == 'unknown'
            assert report.installation_source == 'unknown'
            assert report.authorization_basis == 'none'
            assert report.identity_valid is False and (not report.removivel)
            assert report.reasons == ('replaced_root',)
            assert report.current_installation_id == created.operation_id
            assert report.historical_creation_id == created.operation_id
            original_capture = identification.read_installation_capture
            after_rename = []

            def capture_then_rename(home, name):
                captured = original_capture(home, name)
                target.rename(root / 'concurrent')
                after_rename.append(inventory(home))
                return captured
            with patch.object(identification, 'read_installation_capture', capture_then_rename):
                try:
                    identify_skill(home, 'user-fixture', policy=policy)
                except SkillMutationError as error:
                    assert error.kind == 'conflict', error.kind
                    assert 'repita' in str(error).lower()
                    assert str(root) not in str(error)
                else:
                    raise AssertionError('Desaparecimento concorrente nao foi recusado')
            assert inventory(home) == after_rename[0]
            assert (root / 'concurrent' / 'SKILL.md').read_bytes() == source.read_bytes()
        finally:
            db.close()
"""
        result = self.run_in(
            "-",
            input_text=script,
            entrypoint="/opt/kairos/.venv/bin/python",
            extra=("--tmpfs", "/tmp"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_filesystem_rejects_real_bind_mount_on_same_device(self):
        script = """
import os
import subprocess
import tempfile
from pathlib import Path
from kairos_filesystem.contract import FilesystemError, TreeLimits
from kairos_filesystem.tree import capture_tree, observed_mount_id
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    source = home / 'outside'
    source.mkdir()
    (source / 'preserve').write_bytes(b'keep')
    tree = home / 'tree'
    tree.mkdir()
    mount = tree / 'mounted'
    mount.mkdir()
    subprocess.run(['mount', '--bind', str(source), str(mount)], check=True)
    try:
        parent = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
        child = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert os.fstat(parent).st_dev == os.fstat(child).st_dev
            assert observed_mount_id(parent) != observed_mount_id(child)
            try:
                capture_tree(parent, 'tree', limits=TreeLimits(100, 100, 10, 3), include_contents=False)
            except FilesystemError as error:
                assert error.kind == 'conflict'
            else:
                raise AssertionError('bind mount aceito')
            assert (source / 'preserve').read_bytes() == b'keep'
        finally:
            os.close(child)
            os.close(parent)
    finally:
        subprocess.run(['umount', str(mount)], check=True)
"""
        result = self.run_in(
            "-c",
            script,
            entrypoint="/opt/kairos/.venv/bin/python",
            extra=("--privileged",),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    # -- o bug do gateway em dobro ----------------------------------------

    def test_o_gateway_sobe_UMA_vez_so(self):
        """Dois gateways disputariam o mesmo state.db e o mesmo ledger.

        O bug: no caminho PID 1 o dispatcher passava o wrapper como main
        program mesmo sem comando, e o wrapper assume `gateway` quando não
        recebe argumentos — somando-se ao serviço supervisionado main-kairos.
        Só aparece no container de verdade, com a árvore do s6 no ar.
        """
        name = "kairos-teste-gateway-unico"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        try:
            up = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "-e",
                    "KAIROS_WEB_TOKEN=teste-nao-secreto",
                    self.IMAGE,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(up.returncode, 0, up.stderr)
            # A árvore do s6 precisa assentar antes da contagem.
            deadline = time.time() + 60
            procs = ""
            while time.time() < deadline:
                ps = subprocess.run(
                    [
                        "docker",
                        "exec",
                        name,
                        "sh",
                        "-c",
                        'for d in /proc/[0-9]*; do tr "\\0" " " < $d/cmdline 2>/dev/null; echo; done',
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                procs = ps.stdout
                if "kairos dashboard" in procs and "kairos gateway" in procs:
                    break
                time.sleep(2)
            gateways = [ln for ln in procs.splitlines() if "kairos gateway" in ln]
            self.assertEqual(
                len(gateways),
                1,
                f"esperado 1 gateway, achado {len(gateways)}:\n" + "\n".join(gateways),
            )
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

    # -- o bug de UID ------------------------------------------------------

    def _uid_probe(self):
        return (
            'cat > /opt/kairos/.venv/bin/kairos <<"EOF"\n'
            "#!/bin/bash\nid -u\nEOF\n"
            "chmod +x /opt/kairos/.venv/bin/kairos\n"
            "mkdir -p /opt/data && chown 10000:10000 /opt/data\n"
        )

    def test_rf08_root_derruba_para_uid_10000_na_imagem_real(self):
        r = self.run_in("-c", self._uid_probe() + "kairos login")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "10000", r.stderr)

    def test_rf11_o_opt_out_mantem_root_na_imagem_real(self):
        r = self.run_in("-c", self._uid_probe() + "KAIROS_DOCKER_EXEC_AS_ROOT=1 kairos login")
        self.assertEqual(r.stdout.strip().splitlines()[-1], "0", r.stderr)

    def test_sem_mecanismo_de_queda_o_shim_RECUSA_em_vez_de_virar_root(self):
        """Rodar como root "com um aviso" recria o bug em silêncio prático."""
        script = (
            self._uid_probe()
            + "mv /command/s6-setuidgid /command/.x\n"
            + "mv /package/admin/s6/command/s6-setuidgid /package/admin/s6/command/.x\n"
            + "mv /usr/bin/setpriv /usr/bin/.x\n"
            + "kairos login\n"
        )
        r = self.run_in("-c", script)
        self.assertEqual(r.returncode, 77)
        self.assertIn("Recusando", r.stderr)
        self.assertIn("KAIROS_DOCKER_EXEC_AS_ROOT=1", r.stderr)

    # -- ciclo de vida s6 --------------------------------------------------

    def _stub_available(self) -> bool:
        return (
            subprocess.run(
                ["docker", "image", "inspect", self.STUB], capture_output=True, check=False
            ).returncode
            == 0
        )

    def test_rf21_o_container_herda_o_exit_code_do_main_program(self):
        if not self._stub_available():
            self.skipTest(f"imagem {self.STUB} não construída")
        r = self.run_in("boom", image=self.STUB, entrypoint=None)
        self.assertEqual(r.returncode, 42)

    def test_rf19_os_servicos_supervisionados_rodam_como_10000(self):
        if not self._stub_available():
            self.skipTest(f"imagem {self.STUB} não construída")
        r = self.run_in("--version", image=self.STUB, entrypoint=None)
        saida = r.stdout + r.stderr
        self.assertIn("GATEWAY uid=10000", saida)
        self.assertIn("DASH uid=10000", saida)
        self.assertIn("RUNTIME uid=10000", saida)

    def test_broker_externo_suprime_apenas_o_runtime_interno(self):
        if not self._stub_available():
            self.skipTest(f"imagem {self.STUB} não construída")
        r = self.run_in(
            "--version",
            image=self.STUB,
            entrypoint=None,
            extra=("-e", "KAIROS_RUNTIME_EXTERNAL=1"),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        output = r.stdout + r.stderr
        self.assertIn("GATEWAY uid=10000", output)
        self.assertIn("DASH uid=10000", output)
        self.assertNotIn("RUNTIME uid=", output)

    def test_o_cont_init_roda_na_ordem_e_todos_saem_zero(self):
        if not self._stub_available():
            self.skipTest(f"imagem {self.STUB} não construída")
        r = self.run_in("--version", image=self.STUB, entrypoint=None)
        saida = r.stdout + r.stderr
        ordem = [
            n
            for n in ("01-kairos-setup", "015-supervise-perms", "02-reconcile-profiles")
            if f"running /etc/cont-init.d/{n}" in saida
        ]
        self.assertEqual(ordem, ["01-kairos-setup", "015-supervise-perms", "02-reconcile-profiles"])
        for n in ordem:
            with self.subTest(script=n):
                self.assertIn(f"/etc/cont-init.d/{n} exited 0", saida)

    def test_rf01_rf02_o_caminho_nao_pid1_avisa_e_AINDA_executa(self):
        if not self._stub_available():
            self.skipTest(f"imagem {self.STUB} não construída")
        r = self.run_in("boom", image=self.STUB, entrypoint=None, extra=("--init",))
        self.assertIn("INDISPON", r.stderr)
        self.assertEqual(r.returncode, 42, "degradou, mas o comando tinha de rodar")

    def test_rf16_o_marcador_e_escrito_com_dono_e_perms_corretos(self):
        if not self._stub_available():
            self.skipTest(f"imagem {self.STUB} não construída")
        vol = tempfile.mkdtemp()
        try:
            subprocess.run(
                ["docker", "run", "--rm", "-v", f"{vol}:/opt/data", self.STUB, "--version"],
                capture_output=True,
                timeout=120,
                check=False,
            )
            r = self.run_in(
                "-c",
                'cat /opt/data/.container-mode; stat -c "%a %U:%G" /opt/data/.container-mode',
                extra=("-v", f"{vol}:/opt/data"),
            )
            self.assertIn("runtime=s6", r.stdout)
            self.assertIn("uid=10000", r.stdout)
            self.assertIn("supervised=1", r.stdout)
            self.assertIn("644 kairos:kairos", r.stdout)
        finally:
            # O volume ficou com dono UID 10000 (é o ponto do teste), então o
            # host não consegue removê-lo — a limpeza vai de dentro.
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{vol}:/v",
                    "--entrypoint",
                    "/bin/bash",
                    self.IMAGE,
                    "-c",
                    "rm -rf /v/* /v/.[!.]* 2>/dev/null || true",
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
            import shutil as _sh

            _sh.rmtree(vol, ignore_errors=True)

    def test_rf18_KAIROS_UID_sobrescreve_de_verdade(self):
        if not self._stub_available():
            self.skipTest(f"imagem {self.STUB} não construída")
        r = self.run_in(
            "--version", image=self.STUB, entrypoint=None, extra=("-e", "KAIROS_UID=1234")
        )
        saida = r.stdout + r.stderr
        self.assertIn("remapeando", saida)
        self.assertIn("GATEWAY uid=1234", saida)

    # -- o executável real -------------------------------------------------

    def test_o_binario_kairos_EXISTE_na_imagem(self):
        """A Tarefa 07 entregou um container que falhava com 'No such file'
        porque não havia entry point. Agora há."""
        r = self.run_in("-c", "test -x /opt/kairos/.venv/bin/kairos && echo ok")
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)

    def test_codex_real_tem_versao_e_recursos_fixados(self):
        r = self.run_in(
            "-c",
            "codex --version && "
            "test -x /usr/local/lib/codex/codex-resources/bwrap && "
            "test -x /usr/local/lib/codex/codex-resources/zsh/bin/zsh && "
            "test -x /usr/local/lib/codex/codex-path/rg && "
            "test -x /usr/local/lib/codex/bin/codex-code-mode-host",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "codex-cli 0.154.0")

    def test_a_politica_do_worker_embarcada_attesta_os_dois_modos(self):
        """E2E do pacote embarcado: WorkerPolicy produz os dois conjuntos de
        security-opt (protótipo e sandbox interna), com os caminhos/países de
        runtime documentados. Só aparece num container real depois de a própria
        imagem ter sido construída — a deriva entre o que se testa fora e o que
        a imagem copia foi exatamente a classe de bug que o RealImage captura."""
        r = self.run_in(
            "-c",
            "from kairos_runtime.experimental.worker_policy import (\n"
            "    WorkerPolicy, SANDBOX_SECCOMP, SANDBOX_APPARMOR,\n"
            ")\n"
            "IMAGE = 'sha256:' + 'a' * 64\n"
            "NAME = 'kairos-worker-' + 'b' * 32\n"
            "prototype = WorkerPolicy(IMAGE, NAME)\n"
            "sandbox = WorkerPolicy(IMAGE, NAME, sandbox=True)\n"
            "def sec(args):\n"
            "    return sorted(args[i + 1] for i, a in enumerate(args) if a == '--security-opt')\n"
            "proto = sec(prototype.create_args())\n"
            "snds = sec(sandbox.create_args())\n"
            "assert proto == ['no-new-privileges'], f'protótipo: {proto}'\n"
            "assert 'no-new-privileges' in snds, f'base de sandbox: {snds}'\n"
            "assert f'seccomp={SANDBOX_SECCOMP}' in snds, f'seccomp de sandbox: {snds}'\n"
            "assert f'apparmor={SANDBOX_APPARMOR}' in snds, f'apparmor de sandbox: {snds}'\n"
            "assert SANDBOX_SECCOMP == '/opt/kairos/seccomp-runtime.json'\n"
            "assert SANDBOX_APPARMOR == 'kairos-worker-runtime'\n",
            entrypoint="/opt/kairos/.venv/bin/python",
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_runtime_enabled_reflete_o_probe_real_da_plataforma(self):
        name = "kairos-test-runtime-sandbox"
        config = REPO / "tests" / "fixtures" / "container_runtime_enabled.yaml"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        try:
            started = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "-v",
                    f"{config}:/opt/data/config.yaml:ro",
                    "-e",
                    "KAIROS_WEB_TOKEN=teste-nao-secreto",
                    self.IMAGE,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            deadline = time.monotonic() + 30
            status = None
            while time.monotonic() < deadline:
                result = subprocess.run(
                    ["docker", "exec", name, "kairos", "runtime", "status", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if result.returncode == 0:
                    import json as _json

                    status = _json.loads(result.stdout)
                    break
                time.sleep(0.2)
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    "--user",
                    "10000:10000",
                    name,
                    "/opt/kairos/.venv/bin/python",
                    "-c",
                    "import asyncio\n"
                    "from kairos_runtime.supervisor import CodexSupervisor\n"
                    "async def main():\n"
                    "    supervisor = CodexSupervisor(codex_home='/opt/data/codex-runtime')\n"
                    "    try:\n"
                    "        await supervisor.probe_sandbox()\n"
                    "    finally:\n"
                    "        await supervisor.aclose()\n"
                    "asyncio.run(main())\n",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                status,
                {
                    "authorized_projects": [],
                    "project_versions": [],
                    "enabled": True,
                    "sandbox_profiles": ["read_only", "workspace_write"],
                    "state": _runtime_state_for_probe(probe.returncode),
                },
            )
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

    def test_o_container_RODA_o_comando_pedido(self):
        r = self.run_in("--version", entrypoint=None)
        saida = r.stdout + r.stderr
        self.assertIn("kairos 0.1.0", saida)
        self.assertIn("[container]", saida, "o fast path detecta o modo container")
        self.assertNotIn("No such file", saida)

    def test_o_doctor_migra_o_schema_dentro_do_container(self):
        r = self.run_in("doctor", entrypoint=None)
        saida = r.stdout + r.stderr
        self.assertIn("/opt/data/state.db", saida)
        self.assertIn("integrity_check", saida)

    def test_comando_implementado_roda_com_efeito_real_no_container(self):
        r = self.run_in("backup", entrypoint=None)
        saida = r.stdout + r.stderr
        self.assertIn("Backup de /opt/data", saida)
        self.assertIn("archive:", saida)
        self.assertIn("tamanho:", saida)

    def test_comando_desconhecido_falha_barulhento_em_vez_de_mudo(self):
        r = self.run_in("nao-existe", entrypoint=None)
        saida = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid choice", saida)
