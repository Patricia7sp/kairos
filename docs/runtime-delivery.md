# Runtime delivery: operator runbook

The Web and runtime session produce a review package. A trusted operator exports
source, approves the exact review ID, runs tests in a new Git worktree, and then
explicitly publishes the tested commit as a draft PR. None of these commands
merge a PR or deploy a service. Run them on the operator host with its Git
identity; GitHub authentication is needed only for publication.

## Export and select an immutable source version

```bash
kairos runtime project-export \
  --repo /home/operator/src/app --revision main \
  --catalog /home/operator/project-catalog --json
```

The result includes `revision`, `baseline_fingerprint`, and `cwd`. Layout is
`<catalog>/<full-commit>/workspace`, with sibling `project.json`. Repeating an
export verifies the existing version instead of overwriting it. Git links,
submodules, `.git` paths and tracked snapshot-excluded files (including `.env*`)
are rejected. Export reads the commit, so uncommitted source edits are excluded.

Configure `agent_runtime.project_catalogs` with the host-visible catalog paths.
For Docker, mount the catalog read-only through `KAIROS_RUNTIME_PROJECT_CATALOG`
and configure the runtime-visible `/projects/versions` path. Restart an idle
broker to discover new versions. Choose the exact exported workspace in the Web
selector or with `kairos runtime session create --cwd ... --sandbox workspace_write`.
Authorization covers each discovered workspace exactly; catalog descendants do
not become arbitrary authorized session roots.

## Inspect and save the complete review

Wait until the session is idle with a completed checkpoint. The Web's
**Revisar alterações** action shows the revision, review ID, file summary and
diff; **Baixar pacote** downloads the complete JSON. Alternatively:

```bash
mkdir -p /home/operator/delivery /home/operator/worktrees
kairos runtime changes --session SESSION_ID \
  --output /home/operator/delivery/task-review.json --json
```

Inspect the whole diff, file paths, binary notices and mode changes. The bundle
contains base64 file payloads, old/new hashes and normalized modes. Binary
notices require inspecting the actual payload when its contents matter. An ID
proves package integrity, not authorship or safety. An active turn, missing
legacy baseline, or oversized review returns an error; no partial review is
saved. Failed or interrupted turns are not automatically replayed.

The CLI writes canonical compact JSON with mode `0600`, and never overwrites an
existing output. Reviews, including their ID, are limited to **512 KiB**; IPC is
still limited to **1 MiB**. Application checks raw file size before JSON parsing,
rejects duplicate keys, and validates the package again. Preserve the original
compact file; pretty-printing can push a near-limit package over the raw limit.

## Apply exact approval and run explicit tests

Copy the reviewed full ID into `--approve`. Choose an unused branch and worktree,
plus a new receipt filename. Their parent directories must already exist and
must not contain symlinks. The worktree must be outside the source repository,
its Git directory, and exported source. The receipt must be outside the worktree.

```bash
kairos runtime apply \
  --repo /home/operator/src/app \
  --bundle /home/operator/delivery/task-review.json \
  --approve FULL_REVIEW_ID \
  --branch runtime/task-001 \
  --worktree /home/operator/worktrees/task-001 \
  --test 'uv run pytest -q tests/test_feature.py' \
  --test 'uv run ruff check app tests' \
  --receipt /home/operator/delivery/task-receipt.json --json
```

Apply reconstructs the stated Git commit in a disposable catalog, verifies the
baseline fingerprint, old contents/modes and displayed diff, then creates a new
branch/worktree. It applies only the approved additions, edits and deletions,
including file/directory transitions and executable modes. It verifies files and
the Git tree before and after tests, stages exact reviewed paths and commits
with checkout/commit hooks and commit signing disabled. The original checkout
is not switched or edited.

At least one nonempty test command is mandatory. Each `--test` is parsed with
`shlex.split` and executed as argv in the worktree, without a shell. Pipes,
redirects and variable expansion are not interpreted. Tests are trusted host
commands: review them before authorizing them. They have the operator's host
access, rather than the runtime worker sandbox. Keep credentials out of test
arguments because argv is retained in the receipt and draft PR. Test stdout and
stderr are suppressed to keep CLI JSON and evidence free of diagnostic secrets;
rerun a failing command manually in the retained worktree to see diagnostics.
Each test has a one-hour timeout.

Ignored generated files may remain. Tracked edits or nonignored extra files,
including edits hidden with Git index flags, are rejected. Git content filters
or checkout transformations that make files differ from the approved bytes are
also rejected. Empty reviews cannot create a delivery commit.

The immutable-create `0600` receipt records the review ID, baseline fingerprint,
base commit, repository/worktree/branch, resulting commit/tree, changed paths,
and successful test argv/exit codes. Its evidence digest is recorded in the
commit message. This binds later publication to the tested evidence; it is not
a cryptographic operator signature. Keep the receipt and worktree together and
keep concurrent writers out during apply or publish.

## Publish the tested commit as a draft PR

Inspect the resulting commit and receipt, then explicitly publish:

```bash
kairos runtime publish \
  --receipt /home/operator/delivery/task-receipt.json \
  --title 'Fix feature behavior' --base main --json
```

Publication checks receipt schema and commit-bound evidence, parent/tree,
branch, worktree identity, clean status and actual tracked bytes/modes. It
requires one matching origin fetch/push destination. It resolves that origin
with `gh repo view` and explicitly targets the returned repository in subsequent
PR operations. `GH_REPO` cannot redirect the PR.

The command pushes only the receipt's commit to its branch without force,
mirroring or automatic tag publication, then uses `gh pr create --draft` with a
temporary body file containing review, commit, change and test evidence. Title,
base and head are separate arguments. The result contains the PR URL. Configure
Git/GitHub operator access separately; runtime auth files are never copied into
reviews, receipts or PR bodies. Git and gh diagnostics are sanitized.

## Failure and retry boundaries

- Invalid approval, baseline, paths, existing output/branch, or missing tests
  fail before creating the worktree. Existing files are preserved.
- A failed test or unexpected mutation leaves the worktree for inspection and
  creates no delivery commit or receipt. Fix the underlying change in a new
  reviewed package; apply it with a new branch/worktree/output set. Remove a
  failed worktree/branch manually only after preserving anything needed.
- A filesystem or process failure may leave a partial new worktree, or a commit
  without a receipt if receipt creation fails. Inspect those artifacts; apply
  does not resume them or overwrite a receipt. Repeat from the approved package
  with new paths and a new branch after resolving the cause.
- If push succeeds but gh fails, rerun the same publish command. A remote branch
  already at the receipt commit is accepted. An existing matching open draft PR
  is returned; a different commit, base, or nondraft PR is rejected.
- Commit/branch/tree drift or an edited receipt is rejected. Do not edit a
  receipt to accommodate new work; obtain and approve a new review and rerun the
  application/tests workflow. Ignored generated outputs do not require cleanup.

Git operations have bounded output and a 30-second timeout; gh has a two-minute
timeout and bounded captured output. A timeout may happen after a remote side
effect, so inspect origin/PR state before retrying. These commands assume
operator-owned local repositories and directories; they do not defend against a
host administrator concurrently rewriting Git metadata or test evidence.
