# Remoção segura de plugins e skills — evidências de implementação

Código revisado: `7de35ca..023dfe3`. Os dois planos foram executados em
worktree isolado. Nenhuma skill ou plugin de produção foi removido.

## Resultado

`plugins remove NOME --yes` retira uma instalação sem importar seu código,
preserva os dados separados e registra provas privadas duráveis. Callbacks
carregados precisam de reinício. `skills remove NOME --yes` conserva a árvore
completa no SQLite; `skills rollback ID` restaura bytes, modos e diretórios
sem sobrescrever um destino ocupado ou uma instalação posterior. O histórico
comum e a recuperação abrangem criações, retiradas e restaurações. Tombstones
bundled sobrevivem ao desaparecimento/reaparecimento do bundle no sync.

## Revisão

Todas as tarefas passaram por revisão independente de especificação e qualidade.
A revisão integral encontrou quatro problemas e uma divergência menor:
sidecars SQLite na leitura, teto insuficiente de metadata de plugin,
classificação incorreta de estado inseguro, caminhos POSIX impossíveis em
provas e registro de capability escritora no reader. Uma única rodada corrigiu
os cinco; a revisão restrita aprovou o delta sem novas regressões importantes.

O reader usa cópia temporária privada de DB+WAL, validada por descritores e
provas conjuntas antes/depois. Custa I/O e espaço proporcionais ao banco e
pode recusar escrita concorrente; não ignora commits WAL nem cria sidecars no
home. O teto de metadata cobre o pior inventário permitido (~495 MB), sem
carregar todo o conteúdo de plugins na memória. Sistemas sem as primitivas e
filesystems locais atestáveis recusam a operação.

## Validação local

- Python 3.11 e Codex pinado 0.154.0. O Codex local diferente fez dois testes
  recusarem o runtime na primeira execução; ambos passaram com o pacote
  SHA-verificado da imagem, sem enfraquecer o guard de versão.
- Suíte inteira: 3686 passed, 16 skipped, 28 deselected; 6119 subtests passed.
- Correções finais: 205 passed, 152 subtests; reader: quatro verificações adicionais.
- Ruff check/format, shellcheck, gate de recall e uv.lock: verdes.
- TypeScript e Vitest: ui-tui 17, web 204, desktop 18 testes aprovados.
- Hadolint 2.12.0: Dockerfile real e stub aprovados; builds/checks real, stub,
  broker e worker integram os gates locais.
- Sessões Docker offline: 26 passed, 1 skipped; modelo simulado, sem credenciais.
- Imagem final e integração: consultar registro de gate abaixo.

O opt-in de sandbox interno não foi exercitado: um container descartável
recusou aplicar `kairos-worker-runtime` neste host. A configuração de segurança
do host não foi alterada. `runtime_live` permanece excluído por exigir
credenciais reais. Skips não são evidência desses caminhos.

## Decisões do coordenador

Registro integral das decisões preservado dos dois ledgers antes de qualquer
limpeza. Há decisões repetidas porque cada plano mantém seu próprio registro;
as referências permitem reconstruir a ordem dentro de cada plano. Cada entrada
declara a escolha, o motivo e o custo caso ela esteja errada.

1. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:23`

   Ruling: Run independent filesystem/plugin and journal workers concurrently as explicitly requested by the user and approved plan, rather than the skill's blanket sequential-worker default — disjoint file ownership and controller-serialized commits prevent conflicts — wrong ownership costs rework/integration conflicts.

2. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:24`

   Ruling: Use Task headings for extraction scripts without changing task content — helper recognizes English Task only — wrong extraction would omit task requirements, so inspect briefs before dispatch.

3. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:25`

   Ruling: New SQLite journal worker does not modify kairos_cli/skill_mutations.py; controller assigns capability integration to S4 after journal review — current CLI remains compatible until integration — wrong integration timing costs temporary feature unavailability caught by CLI tests.

4. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:32`

   Ruling: Extend P1 to include Dockerfile COPY for new kairos_filesystem package and a real-image import check — package discovery alone does not copy source into the container — omitting this costs a production import failure. Worker owns Dockerfile/test_container.py for this minimal change; image check verified after build.

5. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:39`

   Ruling: Permit Linux inotify parent observation to attest mkdir/open creation before using a directory — mkdir cannot atomically return its inode/FD and reviewer reproduced real replacement — lack/overflow/event divergence fails closed; wrong mechanism costs safe refusal or identity violation, covered by real substitution tests and scoped re-review.

6. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:40`

   Ruling: package fixes from94d082f to exclude parallel journal commit; git diff880902c..94d082f for all3fixfiles is empty, so complete logicalfixdiff preserved — wrong filter costs omittedfinding, empty diff check recorded. Rebuildsession91761 running for updatedimage tests.

7. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:42`

   Ruling: Whitelist only local filesystems with trustworthy inotify; overlay cannot attest its backing layers, so refuse restore there and expose probe before removal — guarantees outrank broader platformacceptance — wrongclassification costs safe refusal; implemented tests and operationalhelp requiredinS4. Imagepositives use tmpfs; production smokeshome mustbe tempdirwithin mounteddata volume supportedFS, never overwrite realhome.

8. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:43`

   Ruling: Fixround2 reviewpackage c42e049..04d2abb excludesjournalfix; filesystem sources unchanged frombcc405d toc42e049 — no ownfixomitted — wrongfilter costs missedfinding.

9. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:46`

   Ruling: Allow plugin adapter to consume the reviewed internal _create_directory helper with ExitStack and observed mount ID, rather than duplicate its security mechanism — this is an internal same-repository dependency and avoids concurrent filesystem edits — an incompatible helper change costs adapter failure, covered by focused tests and final integration review.

10. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:47`

   Ruling: Extend P2 ownership to kairos_plugins/__init__.py for lazy compatible exports — importing the package otherwise eagerly imports its loader on remove — a compatibility error costs plugin import regressions, gated by existing plugin suites and a real subprocess import test.

11. `.superpowers/sdd/2026-10-10-remocao-plugins/progress.md:52`

   Ruling: Authorize minimal backwardcompatible optional parentchain guard in generic delete_verified_tree and caller plugin check_chain beforeeverymutation to close proven movedancestor cleanup gap — consumer homechain is outside genericparentFD boundary — wrongguard costs deleted movedtree; actual renameancestor afterfirstunlink regression and scopedhelper rereview gate fix. UUIDoperationdirectory must exclusivecreation preserving anyorphan.

12. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:28`

   Ruling: Run independent workers concurrently under the user's explicit multiagent request and approved plan; maintain exclusive file ownership and serialize commits — incorrect ownership costs reviewable integration rework.

13. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:29`

   Ruling: Defer skill CLI changes from S1 to S4 to preserve exclusive integration ownership — journal/repository capability registration is implemented first — incorrect deferral costs temporary feature availability, verified by S4 tests.

14. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:30`

   Ruling: Use English Task headings for script extraction; contents preserved — wrong extraction costs missing requirements, inspect generated briefs.

15. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:37`

   Ruling: Add Provenance.BUNDLED and protect every non-SEDIMENT provenance from autonomous curation — approved spec requires preserving bundled origin and existing enum has only USER/SEDIMENT; reusing either would misstate origin/ownership — wrong classification costs incorrect origin metadata or curation permissions; behavioral domain tests gate it. Journal worker owns ownership.py/test_domain.py for this minimal additive change.

16. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:42`

   Ruling: Scope fixpackage frombcc405d (samejournal sources as94d082f, confirmed emptydiff for3files) to excludefilesystemfix — every ownfixretained — wrongfilter costs omittedfinding.

17. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:47`

   Ruling: Update S1 tests to compare legacy-only evidence within common history and query canonical preservation directly when schema version is future — the approved facade now merges families and refuses incompatible reads — an incorrect expectation change costs false compatibility assurance, so preserve byte/ID assertions rather than remove them.

18. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:48`

   Ruling: Replace unapproved 4096-byte whole-path/20MiB JSON bounds with bounds derived from depth32, 255-byte POSIX components and4096entries, including worst-case JSON escaping — descriptor-based valid paths and canonical manifests can exceed prior caps — wrong derivation costs refusal of valid snapshots or excessive metadata allocation; behavioral long-path and >20MiB SQLite roundtrip gate the change. Journal follow-up also owns removal_contract.py; content/entry/depth limits remain exact spec values.

19. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:52`

   Ruling: Begin independent S3 descriptor IO and origin work while S2 finishes; delay shared service integration until its review clears — both tasks depend on the completed journal/filesystem, and new-file ownership is disjoint — an interface adjustment costs adapter rework, while ownership embargo and final task review prevent competing writes.

20. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:59`

   Ruling: Resume orphaned S3 partial new-file work with /root/skill_tree_service at a5cc7fe, preserving test_skill_removal_io.py and embargoing mutations.py/mutation_recovery.py until S2 approval — live roster has no previous S3 agent and current workers disclaim ownership; user async coordination question remains unanswered; no new writes observed to S3 — stale-owner mistake costs collision/rework, worker must stop if external writes observed.

21. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:62`

   Ruling: Start disjoint S4 read-only ledger/sync/composition partial work with /root/skill_cli_sync at9de83c7 while S3 service finishes; main/handlers/commands/decisions embargoed until pluginworker ends, S3 files excluded — commonfacade approved and consumers can implement against fixed contracts; final validation/review waitS3 — wrong contract assumption costs consumer rework caught by integrated tests. Ownership removal_state.py/sync.py/CLIskill_mutations/newCLI+sync tests only.

22. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:63`

   Ruling: Extend S3 ownership minimally to mutation_contract.py to add denied error kind and use it for rejected actor/confirmation — current whitelist and validate_actor input force exit2, approvedspec requires77 beforeIO — wrong mapping costs legacy classification regressions; behavioral actor/CLI tests must retain noIO assertions. S4 informed denied77 mapping.

23. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:64`

   Ruling: Extend S3 facade minimally with has_later_installation(remove_id) over validated committed timeline, preserving approved current_removal API — REMOVE followed by CREATE then ROLLBACK leaves absentname but repeatREMOVE/newRESTORE must not reuse superseded removal; changing current_removal would break approved origin/tombstone contract — wrong timelineguard costs stale snapshot revival; real service regression with latercreate/rollback and prior restore idempotency gate it. S3 owns this repository helper only; no projection semantic rewrite.

24. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:65`

   Ruling: S3 may update two temporary S2 writerguard state assertions PREPARED->CONFLICT when full4action reconciliation now inspects syntheticproof and detects divergence — provisionalS2 refused before inspecting, finalspec requires durableconflict — wrongclassification costs weakened isolation tests; preserve contents/IDs/no-delete asserts and real pendingvalid subprocess recovery tests. Ownershipprojectiontest limited2expectations. S3sixsuite GREEN58passed37subtests, laterinstallationRED1failed->GREEN.

25. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:66`

   Ruling: Extend the same generic optionalguard mechanism to restore_tree before every creation/write/chmod/fsync, with S3passingfiles.check — namedrace moves .skill-mutations duringstage and currenthelper knows only descendants of stagingFD — wrongguard costs creation outside observedhome; real ancestor-motion stage regression + scopedsharedhelper review +image rerun gate change. SingleFSwriter /root/plugin_resume; S3 consumer only.

26. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:67`

   Ruling: Inspect and allow literal backslash in generic LinuxPOSIX path components while retaining slash/NUL/dot escape rejection and highlevelplugin/skill namevalidators — S3observed generic helper rejects ordinaryrelativefilename accepted by snapshotcontract, specpreserves paths — wrongallowlist costs pathvalidation regression, gated real capture/restore/delete backslashasset and unchanged highlevelname tests. FSowner pluginfixworker only.

27. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:68`

   Ruling: Final restore capture streams hashes without reconstructing untrustedbytes, compares full snapshotinventory then returns knownvalidatedsnapshotcontents with observednewidentities — maxauxiliaryfilebytes could otherwise allocate concurrentlyinflatedSKILL beyond64KiB before consumerrejects — wronghashproof costs accepting divergentcontent; actual inflatedSKILL +largeaux regression and unchanged roundtrip guard callback tests gate sharedhelperchange. Generichelper remains unaware of skill rules.

28. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:72`

   Ruling: Scope S3package37c4aba..bb4667b rather than originaldispatcha5cc7fe — all11S3files only have approvedS2fix interimdifferences (byteequal diffs a5..37 vs a5..9de confirmed), allS3changes committedonlybb — wrongfilter costs omittedownchange, equalityproof and commit11paths gate completeness.

29. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:77`

   Ruling: Extract the already SHA-verified pinned Codex package from kairos:test into private /tmp and prepend its bin for local CI — system Codex differs from enforced0.154.0; do not weaken version guards — wrong environment costs unrepresentative runtime tests, binaryversion and full real binary probes gate it.

30. `.superpowers/sdd/2026-10-10-remocao-skills/progress.md:78`

   Ruling: Reader uses private temporary descriptor-copy of DB plus committed WAL with joint before/after identity/stat verification and explicit concurrent-write refusal; SQLite only opens the copy — immutable on original could drop tombstones and mode=ro creates sidecars — wrong snapshot attestation costs stale projection, gated WAL-visible/no-home-effects/concurrent-change behavior tests. Copy cost and possible busy refusal accepted.

## Gate final da imagem

`scripts/ci.sh` completo encerrou com exit 0. Imagem final
`sha256:cfdcf0607fca0fbd368bb87f43d8d5f047fa22e3775784cad71fa0c95ded0ab1`: 27 testes e 26 subtestes de integração passaram, sem skips.
O smoke adicional, executado como UID 10000 em tmpfs descartável, passou
por add → editar binário/modos/diretório vazio → remove → history → destino
ocupado recusado → rollback → comparação integral; também verificou
rollback antigo recusado, tombstone após bundle sair/voltar e retirada de
plugin quebrado com dados preservados. Nenhum home de produção foi usado.
O broker foi reconstruído com as mesmas fontes finais.

CI remoto, backup consistente/restauração, merge e implantação são etapas
operacionais posteriores; este registro não antecipa seu sucesso.
