# Registro de decisões do Agent Runtime

Decisões registradas durante a implementação do plano de 2026-09-05.
O texto original preserva a justificativa e o custo caso a decisão precise ser
revista. O escopo permanece local e de usuário único.

1. Ruling: Rename plan headings Tarefa to Task for the skill extractor — parser requires English marker — cost if wrong: documentation-only rename.

2. Ruling: Add policy identity helper to capture canonical path/device/inode and revalidate before lease — string-only authorize_directory cannot detect replaced directories — cost if wrong: small API adaptation before integration.

3. Ruling: Add explicit consent argument at repository create boundary for broad sandbox — the listed RuntimeSession has no consent field — cost if wrong: callers need one extra keyword.

4. Ruling: Negotiate known capabilities as a subset rather than requiring every optional capability — spec says only offer/invoke declared operations, not require reasoning/tools/etc for version compatibility — cost if wrong: stricter adapters can enforce their own required capability set before execution.

5. Ruling: Inherit host ownership lock fd into App Server — abrupt host death must not allow a second host while old Codex is alive — cost if wrong: runtime availability waits for orphan child exit; does not permit unsafe takeover.

6. Ruling: Allow internal RuntimeObservation interrupted when mapping Codex terminal interrupted — avoids falsely claiming user-confirmed cancellation — cost if wrong: small initial-contract enum/service handling extension before release.

7. Ruling: Extend lease release with explicit confirmed_inactive keyword and durable inactivity marker for interrupted outcomes — execution inactivity and outcome certainty are different facts; relabeling interrupted would falsify history — cost if wrong: one pre-release schema field and a service-call keyword to adapt.

8. Ruling: Verify generated schema JSON mechanically and review handwritten diff separately — full review package is737KiB mostly repeated generated definitions; all30 JSON files cmp-identical to fresh isolated0.153.4 output — cost if wrong: protocol-specific schema properties still need targeted inspection and live acceptance; complete unfiltered package remains available.

9. Ruling: Put runtime session creation/read orchestration in service5, with small public entrypoints for host6 — plan requires thread/start failure recovery but omits create from service's listed signatures; duplicating orchestration in host would split durable ownership — cost if wrong: small internal API adjustment before IPC implementation.

10. Ruling: Reuse existing private run_persistent_cleanup coordinator without moving/changing provider code — it is generic lifecycle infrastructure already used across integration, not provider retry; avoids duplicated cancellation logic and unrelated refactoring — cost if wrong: one private cross-package dependency to extract later, explicit task-cancellation outcomes still need handling/testing.

11. Ruling: Validate effective workspace roots rather than literal serialized roots — real0.153.4 normalizes configured cwd out of additional writableRoots and reports runtimeWorkspaceRoots=[cwd]; official config describes writable_roots as additional — require matching cwd, only []/[cwd] additional roots, if runtimeWorkspaceRoots supplied require exactly[cwd], preserve strict network/temp exclusions — cost if wrong: small compatibility validator adjustment; no extra directory becomes allowed.

12. Ruling: Add attach_turn(session, local_turn_id, external_turn_id)->RuntimeObservation to runtime protocol/adapter — recovered turns need explicit subscription and ID binding before resume without calling turn/start — cost if wrong: small pre-release contract extension for the sole adapter and test fakes.

13. Ruling: Recovery receives fail-closed inactivity_confirmed(generation) host attestation, combined with external terminal evidence — a new process snapshot alone cannot prove an orphan old generation stopped — cost if wrong: one host/service constructor callback; durable dispatch journal records generation for reconstruction.

14. Ruling: Add narrow fenced lease recovery takeover for an existing turn — queued-only claim cannot restore active observation after recovery, and must not resend — require exact predecessor ownership, same durable turn, filesystem revalidation and explicit current-owner/orphan-exclusion evidence; advance fencing generation without changing send identity — cost if wrong: small internal lease API extension and stronger lifecycle-write fencing tests.

15. Ruling: Order recovery snapshot barrier at originating RPC response dispatch — marker inserted only after awaiter resumes can overwrite newer already-buffered deltas — cost if wrong: one internal response/subscription hook and deterministic ordering tests; no text-based deduplication.

16. Ruling: Fix approval-delivery acknowledgement after terminal release in Task5 producer before completing Task6 — real server may complete immediately after reply; delaying fake hides valid ordering failure — cost if wrong: narrow service/store acknowledgement contract adjustment and one scoped review, preserving origin/turn/decision fencing and no resend.

17. Ruling: Wire existing supervisor restart/health lifecycle from host6 — spec99–100 requires monitoring and restart, but focused rg shows no production .restart caller and host status staysready byservicepresence afterchildexit — cost if wrong: bounded hostmonitor integration with admission/status/recovery coordination and tests; no new supervisor algorithm or turn replay. Host agent informed to implement only after producerfix/review, not inparallel.

18. Ruling: Add public service pause_runtime(generation) async and resume_runtime() dispatch gate for host restart — recovery must await prior-generation runners without permanently closing supervisor; queued/not_sent must survive — cost if wrong: narrow lifecycle API and boundary-race tests. Gate closes beforeawait and synchronizes dispatch boundary; no old-generation runner remains in _lost at recovery, no queued replay/uncertainty invention, host fences admission/inflight mutations; resume only afterrestart+recover.

19. Ruling: Allow narrow additive runtime.status project/sandbox choices during Task9 — its approved brief requires a list of authorized projects but Task8 status has only enabled/state, so the consumer has no source — cost if wrong: small host/HTTP status metadata extension and backend tests, preserving old fields and strict validation; no raw config/auth exposure, new IPC method, or client-controlled allowlist. Per-session capabilities already come from durable metadata. Context recorded before UI dispatch.

20. Ruling: Expose a per-call synchronous runtime-accepted callback from router streaming for CLI cancellation — accepted turn identity must be available before first journal event and global last-turn state would race — cost if wrong: optional backward-compatible router keyword plus CLI wiring/tests; called immediately after successful submit response, never invents identity for ambiguous submit or triggers resend.

21. Ruling: Task9 unavailable durable sessions offer successor, not a locally forced continue — repository admission runtime.py:313 rejects unavailable/ended and missing thread must not be reconstructed; host unavailability is separate and can refresh connectivity, interrupted may continue only when producer admits — cost if wrong: narrow UI gating adjustment, no backend lifecycle mutation or automatic resend. Existing general sessions/messages APIs provide offline durable transcript (server.py:732,805).

22. Ruling: Add bounded sanitized no-login sandbox preflight before container runtime ready — actual image cannot create bwrap namespace but old host would advertise ready until turn failed, violating Task10 fail-closed requirement — cost if wrong: narrow host/supervisor API and startup latency; probe failure disables runtime while preserving host/status/providers, no privilege/capability/sysctl/broad fallback. Container marker+enabled gate, fixed harmless command, dedicated CODEX_HOME, timeout/terminate/kill/reap and static diagnosis required. Original Task10 implementer owns focused TDD and image rebuild, no redundant full suite.
