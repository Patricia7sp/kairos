"""A árvore de comandos, declarada como dados.

`_reversa_sdd/hermes-cli/` (superfície CLI).

**Por que um registro e não 46 módulos.** O legado partiu `main.py` em
`subcommands/*.py` porque o arquivo virou um god-file de 975 KB — a divisão
resolveu um problema de tamanho, não de desenho. O Kairos não tem esse
problema, e 46 arquivos com um `build_*_parser` de dez linhas cada trocariam
um god-file por dispersão.

Declarar a árvore como dados dá três coisas que a divisão em módulos não dá:
a superfície inteira é **legível de uma vez**, é **testável** (dá para afirmar
que um comando existe sem construir o parser), e a contagem deixa de ser
folclore.

⚠️ **Correção de spec.** `hermes_cli/main.py` estava marcado 🔴 *"árvore de
comandos (50 comandos) — não percorrida"*. Percorrida agora, o número não
bate: `hermes_cli/subcommands/` tem **44** módulos de comando (excluídos
`__init__` e `_shared`), e o despacho de topo acrescenta `run` e `tick`. Com
`chat` (alias) e `version`, são **48** comandos — não 50.

A contagem aqui é evidência extraída da fonte, não repetição de uma frase que
ninguém tinha conferido.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["COMMANDS", "Command", "Status", "command_names", "find_command", "leaf_count"]


class Status:
    """O que o Kairos entrega hoje para cada comando.

    A distinção é obrigatória, e vem de uma regra que o próprio projeto
    estabeleceu: **uma ferramenta que reporta sucesso sem efeito é pior que
    uma ferramenta ausente**. Um comando declarado que não faz nada precisa
    dizer isso, não sair com 0 em silêncio.
    """

    IMPLEMENTED = "implemented"
    #: Declarado na superfície, sem implementação ainda. Sai com código
    #: próprio e mensagem que diz o que falta.
    NOT_IMPLEMENTED = "not_implemented"


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    subcommands: tuple[Command, ...] = ()
    status: str = Status.NOT_IMPLEMENTED
    #: Unidade reconstruída que sustenta o comando, quando há.
    unit: str | None = None


def _c(name, help, *subs, status=Status.NOT_IMPLEMENTED, unit=None) -> Command:
    return Command(name, help, tuple(subs), status, unit)


def _sub(name, help, *subs) -> Command:
    return Command(name, help, tuple(subs))


#: A árvore. Ordem alfabética, exceto os de topo, que vêm primeiro por serem
#: o caminho comum.
COMMANDS: tuple[Command, ...] = (
    # --- topo -------------------------------------------------------------
    _c(
        "run",
        "Conversa persistida com o agente (comando padrão)",
        status=Status.IMPLEMENTED,
        unit="interaction-service",
    ),
    _c(
        "chat",
        "Conversa persistida com o agente",
        status=Status.IMPLEMENTED,
        unit="interaction-service",
    ),
    _c(
        "runtime",
        "Sessões persistentes do Agent Runtime",
        _sub("serve", "Sobe o host compartilhado em primeiro plano"),
        _sub("status", "Mostra o estado do host"),
        _sub("login", "Autentica no runtime oficial"),
        _sub("logout", "Encerra a autenticação do runtime"),
        _sub(
            "session",
            "Gerencia sessões persistentes",
            _sub("create", "Cria uma sessão de runtime"),
            _sub("end", "Encerra uma sessão de runtime"),
        ),
        _sub("approve", "Responde explicitamente a uma aprovação"),
        _sub("cancel", "Cancela um turno explicitamente"),
        _sub("watch", "Acompanha o journal a partir de um cursor"),
        _sub("project-export", "Exporta uma revisão Git imutável"),
        _sub("changes", "Salva o pacote completo para revisão"),
        _sub("apply", "Aplica uma revisão aprovada e executa testes"),
        _sub("publish", "Publica um commit verificado como PR draft"),
        status=Status.IMPLEMENTED,
        unit="agent-runtime",
    ),
    _c("tick", "Executa um tick do scheduler e sai", unit="cron", status=Status.IMPLEMENTED),
    _c(
        "version",
        "Mostra a versão, o perfil e o modo de container",
        status=Status.IMPLEMENTED,
        unit="kairos-cli",
    ),
    # --- grupos (extraídos de hermes_cli/subcommands/) --------------------
    _c("acp", "Servidor Agent Client Protocol para editores", unit="acp-adapter"),
    _c(
        "approvals",
        "Regras de aprovação de comando",
        _sub("test", "Veredito de aprovação sem executar"),
        _sub("suggest", "Minera aprovações implícitas do histórico"),
        status=Status.IMPLEMENTED,
        unit="tools",
    ),
    _c(
        "auth",
        "Credenciais de provedor",
        _sub("add", "Adiciona uma credencial"),
        _sub("list", "Lista credenciais do perfil"),
        _sub("migrate", "Migra credenciais legadas para o cofre"),
        _sub("vault-init", "Inicializa o cofre criptografado"),
        _sub("vault-status", "Mostra o estado seguro do cofre"),
        _sub("vault-unlock", "Desbloqueia o cofre nesta execução"),
        status=Status.IMPLEMENTED,
        unit="kairos-cli",
    ),
    _c("backup", "Backup e restauração do estado", status=Status.IMPLEMENTED, unit="kairos-cli"),
    _c("claw", "Automação de browser"),
    _c(
        "config",
        "Configuração",
        _sub("check", "Valida o config.yaml"),
        _sub("edit", "Abre o config no editor"),
        _sub("env-path", "Caminho do arquivo .env"),
        _sub("migrate", "Aplica migrações de schema de config"),
        _sub("path", "Caminho do config.yaml"),
        _sub("set", "Define um valor"),
        _sub("show", "Mostra o valor efetivo e a camada de origem"),
        status=Status.IMPLEMENTED,
        unit="kairos-cli",
    ),
    _c("console", "Console interativo"),
    _c(
        "cron",
        "Jobs agendados",
        _sub("list", "Lista os jobs"),
        _sub("create", "Cria um agendamento de prompt"),
        _sub("remove", "Remove um job preservando o histórico"),
        _sub("history", "Histórico de execuções"),
        _sub("pause", "Pausa um job"),
        _sub("resume", "Retoma um job"),
        _sub("status", "Estado do scheduler"),
        _sub("tick", "Força um tick"),
        _sub("monitor-set", "Define a fonte de monitor de um job"),
        _sub("monitor-clear", "Remove o monitor de um job"),
        _sub("monitor-show", "Mostra o monitor e o estado de verificação"),
        _sub("monitor-run", "Executa a fonte do monitor uma vez"),
        _sub("notepad", "Memória KV durável de um job, persistente entre execuções"),
        _sub(
            "blueprint",
            "Blueprints de automação parametrizados",
            _sub("list", "Lista o catálogo de blueprints"),
            _sub("show", "Mostra as quatro superfícies de um blueprint"),
            _sub(
                "create",
                "Cria um job a partir de um blueprint (slots slot=valor)",
            ),
        ),
        status=Status.IMPLEMENTED,
        unit="cron",
    ),
    _c("dashboard", "Painel web e interface gráfica", status=Status.IMPLEMENTED, unit="web"),
    _c("debug", "Diagnóstico local sem reparos", status=Status.IMPLEMENTED, unit="observability"),
    _c(
        "doctor",
        "Diagnóstico do ambiente e do estado",
        status=Status.IMPLEMENTED,
        unit="kairos-state",
    ),
    _c("dump", "Exporta estado para inspeção", status=Status.IMPLEMENTED, unit="observability"),
    _c(
        "gateway",
        "Gateway de mensageria",
        _sub("run", "Sobe o serviço em primeiro plano"),
        _sub("list", "Plataformas configuradas"),
        _sub("setup", "Configura uma plataforma"),
        _sub("status", "Estado do gateway"),
        _sub("stop", "Solicita drenagem e encerramento"),
        status=Status.IMPLEMENTED,
        unit="providers-gateway",
    ),
    _c(
        "token",
        "Token de acesso do painel web",
        _sub("show", "Mostra o token em uso"),
        _sub("new", "Gera um token novo e imprime o export"),
        _sub("path", "Onde o token fica guardado"),
        status=Status.IMPLEMENTED,
        unit="web",
    ),
    _c("gui", "Aplicativo desktop"),
    _c("hooks", "Hooks de plugin", unit="plugins"),
    _c("import-agent", "Importa configuração de outro agente", status=Status.IMPLEMENTED, unit="plugins"),
    _c("import", "Importa sessões e dados", status=Status.IMPLEMENTED),
    _c("insights", "Métricas de uso e custo", status=Status.IMPLEMENTED, unit="kairos-state"),
    _c("login", "Autentica num provedor", unit="kairos-cli"),
    _c("logout", "Encerra a autenticação", unit="kairos-cli"),
    _c(
        "logs", "Eventos operacionais de Web, Chat, busca e agendamentos", status=Status.IMPLEMENTED
    ),
    _c(
        "mcp",
        "Servidores Model Context Protocol",
        _sub("list", "Servidores configurados"),
        _sub("remove", "Remove um servidor"),
        _sub("test", "Testa a conexão com um servidor"),
        status=Status.IMPLEMENTED,
        unit="mcp",
    ),
    _c(
        "memory",
        "Memória de longo prazo",
        _sub("off", "Desativa a memória"),
        _sub("status", "Estado da memória"),
    ),
    _c(
        "model",
        "Seleção e capacidades de modelo",
        _sub("show", "Mostra a seleção global"),
        _sub("list", "Lista o catálogo local"),
        _sub("refresh", "Atualiza o catálogo de um provedor"),
        _sub("set", "Define o modelo global"),
        _sub("test", "Testa a conexão com um provedor"),
        status=Status.IMPLEMENTED,
        unit="providers-gateway",
    ),
    _c(
        "monitoring",
        "Monitores de fonte nos agendamentos",
        _sub("list", "Lista os jobs monitorados"),
        _sub("status", "Estado de verificação de cada monitor"),
        _sub("test", "Executa a fonte de um monitor uma vez"),
        status=Status.IMPLEMENTED,
        unit="cron",
    ),
    _c(
        "pairing",
        "Pareamento de usuário no gateway",
        _sub("clear-pending", "Limpa os códigos pendentes"),
        _sub("list", "Lista os pareamentos"),
        _sub("revoke", "Revoga um pareamento"),
        unit="providers-gateway",
    ),
    _c("pause", "Pausa a atividade autônoma"),
    _c(
        "peer",
        "Instâncias pares",
        _sub("add", "Registra um par"),
        _sub("list", "Lista os pares"),
        _sub("remove", "Remove um par"),
    ),
    _c(
        "plugins",
        "Plugins instalados",
        _sub("list", "Lista os plugins e o estado de cada um"),
        _sub("install", "Instala um plugin"),
        _sub("remove", "Remove um plugin"),
        _sub("update", "Atualiza um plugin"),
        status=Status.IMPLEMENTED,
        unit="plugins",
    ),
    _c(
        "profile",
        "Perfis isolados",
        _sub("delete", "Apaga um perfil"),
        _sub("list", "Lista os perfis"),
        _sub("show", "Mostra o perfil ativo"),
        status=Status.IMPLEMENTED,
        unit="kairos-cli",
    ),
    _c("prompt-size", "Tamanho do prompt de sistema", unit="agent"),
    _c(
        "security",
        "Auditoria de segurança e vulnerabilidades",
        status=Status.IMPLEMENTED,
        unit="security",
    ),
    _c("setup", "Assistente de configuração inicial", status=Status.IMPLEMENTED, unit="kairos-cli"),
    _c(
        "skills",
        "Skills do agente",
        _sub("add", "Cria uma skill"),
        _sub("install", "Instala uma skill do hub"),
        _sub("list", "Lista as skills"),
        _sub("remove", "Remove uma skill"),
        _sub("tap", "Fontes de skill"),
        status=Status.IMPLEMENTED,
        unit="skills",
    ),
    _c(
        "skin",
        "Tema da interface",
        _sub("list", "Temas disponíveis"),
        _sub("use", "Aplica um tema"),
    ),
    _c("slack", "Integração com Slack", unit="providers-gateway"),
    _c("status", "Estado geral do sistema", status=Status.IMPLEMENTED),
    _c(
        "sync",
        "Sincronização de skills",
        _sub("disable", "Desativa a sincronização de uma skill"),
        _sub("enable", "Ativa a sincronização"),
        _sub("now", "Sincroniza agora"),
        _sub("push", "Envia alterações locais"),
        _sub("status", "Estado da sincronização"),
        status=Status.IMPLEMENTED,
        unit="skills",
    ),
    _c(
        "tools",
        "Ferramentas registradas",
        _sub("list", "Lista as ferramentas e o toolset de cada uma"),
        status=Status.IMPLEMENTED,
        unit="tools",
    ),
    _c("uninstall", "Desinstala o Kairos"),
    _c("update", "Atualiza o Kairos"),
    _c("verify", "Verificação pós-edição de código"),
    _c("webhook", "Endpoints de webhook", unit="providers-gateway"),
    _c("whatsapp", "Integração com WhatsApp", unit="providers-gateway"),
)


def command_names() -> tuple[str, ...]:
    return tuple(c.name for c in COMMANDS)


def find_command(name: str) -> Command | None:
    for c in COMMANDS:
        if c.name == name:
            return c
    return None


def leaf_count() -> int:
    """Comandos invocáveis: o grupo conta quando não tem subcomando."""
    total = 0

    def leaves(command: Command) -> int:
        return sum(leaves(child) for child in command.subcommands) if command.subcommands else 1

    for c in COMMANDS:
        total += leaves(c)
    return total
