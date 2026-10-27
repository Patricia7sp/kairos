"""Intenção explícita e prompt puro da autoria no Chat."""


def parse_learn_request(content: str) -> str | None:
    command, *rest = content.strip().split(maxsplit=1)
    if command != "/learn":
        return None
    if not rest or not rest[0].strip():
        raise ValueError("Uso: /learn <pedido de aprendizado>.")
    return rest[0].strip()


def build_learn_prompt(user_request: str) -> str:
    return (
        "Crie uma skill a partir somente desta conversa ativa e do pedido abaixo. "
        "Não invente comandos, caminhos, APIs ou evidências. Se precisar de esclarecimento, "
        "pergunte e encerre sem criar; o usuário deverá responder e repetir /learn.\n"
        "Produza um SKILL.md conciso com frontmatter YAML: name kebab-case de até 64 "
        "caracteres, description de até 60 caracteres terminada em ponto (conte os caracteres), version semver. "
        "Autor pode ser omitido ou Kairos; nunca infira identidade do ambiente nem licença.\n"
        "Use a ordem canônica: título e introdução, When to Use, Prerequisites, How to Run, "
        "Quick Reference, Procedure, Pitfalls, Verification. Descreva ações executáveis pelas ferramentas do "
        "agente, sem apenas encaminhar para outras skills.\n"
        "Chame skill_create com name e skill_md completo. O JSON total deve caber em "
        "16 KiB. Somente o recibo COMMITTED comprova criação; aguarde a confirmação "
        "humana e respeite a recusa. Apenas uma skill pode ser criada neste /learn.\n\n"
        "Pedido do usuário:\n" + user_request
    )
