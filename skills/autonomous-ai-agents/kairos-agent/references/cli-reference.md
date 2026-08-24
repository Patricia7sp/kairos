# kairos CLI Reference

Live sources when anything looks stale: `kairos --help`, `kairos <command> --help`,
https://hermes-agent.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
kairos [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
kairos chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
kairos setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
kairos model                Interactive model/provider picker
kairos fallback [add|remove|list]  Fallback provider chain
kairos config [show|edit|get|set|unset|path|env-path|check|migrate]
kairos login / logout       OAuth sign-in / clear stored auth
kairos doctor [--fix]       Check dependencies and config
kairos status [--all]       Component status
```

### Tools & Skills

```
kairos tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

kairos skills list|browse|search QUERY|inspect ID
kairos skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
kairos skills config        Enable/disable skills per platform
kairos skills check|update|uninstall|publish PATH
kairos skills tap add REPO  Add a GitHub repo as a skill source
kairos bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
kairos mcp add NAME (--url or --command) | remove | list | test NAME
kairos mcp catalog | install NAME     Curated catalog install
kairos mcp configure NAME             Toggle tool selection
kairos mcp serve                      Run Kairos as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
kairos gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `kairos photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/

### Sessions

```
kairos sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
kairos cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
kairos webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
kairos profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
kairos profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
kairos auth                 Interactive credential manager
kairos auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
kairos auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
kairos desktop / gui        Native desktop app
kairos dashboard            Web admin panel + embedded chat (--stop / --status)
kairos proxy                OpenAI-compatible local proxy backed by an OAuth provider
kairos portal               Quick setup / sign in via Nous Portal
kairos kanban <verb>        Multi-agent work-queue board
kairos project              Named multi-folder workspaces
kairos skin list|use|set    Switch/tweak skins (see references/themes.md)
kairos pets <verb>          Pet mascots (see references/petdex.md)
kairos memory setup|status|off|reset   Memory provider
kairos secrets bitwarden|onepassword   External secret stores
kairos moa                  Mixture-of-Agents slots
kairos hooks / security / backup / import / checkpoints / console
kairos logs [-f] [errors]   View agent/error logs
kairos send                 One-off message through a gateway platform
kairos pairing / plugins / insights / journey / computer-use
kairos acp                  ACP server (IDE integration)
kairos completion bash|zsh|fish
kairos update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `kairos photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `kairos config edit` · [Configuration docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `kairos tools list` · [Tools reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `kairos skills browse` · [Skills catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `kairos model` · [Providers guide](https://hermes-agent.nousresearch.com/docs/integrations/providers) |
| Env variables | `kairos config env-path` · [Env vars reference](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.kairos/logs/gateway.log` (or `kairos logs`) |
| Sessions | `kairos sessions browse` (reads state.db) |
