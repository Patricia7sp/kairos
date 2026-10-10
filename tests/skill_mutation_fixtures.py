"""Conteúdo fictício de criação, sem identidade pessoal."""


def creation_text(
    name: str = "revisar-docs",
    body: str = "Procedimento fictício.",
    *,
    description: str = "Revise documentos.",
    version: str = "0.1.0",
) -> str:
    return f"---\nname: {name}\ndescription: {description}\nversion: '{version}'\n---\n{body}\n"
