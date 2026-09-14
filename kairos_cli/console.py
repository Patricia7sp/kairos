"""Comando `kairos console` — console interativo."""

from __future__ import annotations

import ast
import operator
import sys
from pathlib import Path

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_arithmetic(expression: str):
    """Avalia somente aritmética com literais numéricos; nada mais.

    Recusa qualquer outro nó da AST em vez de fingir um resultado.
    """
    tree = ast.parse(expression, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](visit(node.operand))
        raise ValueError(f"expressão não suportada: {type(node).__name__}")

    return visit(tree)


async def run_console(home: Path, args) -> int:
    home = Path(home)

    subcommand = getattr(args, "console_command", None) or getattr(args, "subcommand", None)

    if subcommand == "start":
        print("Console interativo indisponível neste modo; use `console eval --expression '2+2'`.")
        return 1
    if subcommand == "eval":
        expr = getattr(args, "expression", None)
        if not expr:
            print("É necessário passar uma expressão via --expression.", file=sys.stderr)
            return 1
        try:
            print(_eval_arithmetic(expr))
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
            print(f"kairos: não avaliado ({exc}).", file=sys.stderr)
            return 1
        return 0
    print("Subcomando inválido. Use: console start | console eval --expression <expr>")
    return 1
