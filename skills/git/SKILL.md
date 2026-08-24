---
name: git
description: Automação e controle de versão git
version: 1.0.0
tags: [git, vcs, devops]
---

# Git Automation Skill

## When to Use
Use esta skill para inspecionar repositórios, criar branches, commits semânticos e resolver conflitos.

## Prerequisites
- Git instalado e configurado no PATH.

## How to Run
Execute comandos git usando a ferramenta bash.

## Quick Reference
- `git status`: visualiza arquivos modificados
- `git diff`: exibe diferenças
- `git commit -m "msg"`: registra alterações

## Procedure
1. Verificar status com `git status`
2. Adicionar arquivos modificados
3. Criar commit descritivo

## Pitfalls
- Nunca use git push --force em branches principais.

## Verification
Execute `git log -n 1` para confirmar o commit.
