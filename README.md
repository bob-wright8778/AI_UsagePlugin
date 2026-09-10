# AI_UsagePlugin

An Omarchy shell bar-widget plugin (`bwright.ai-usage`) that shows Claude Code and GitHub Copilot
(JSI account) usage from one bar icon and one popup panel, themed to match the active Omarchy theme.
Personal-use scope — not intended for distribution.

Spec and design decisions live under `dev/` in the AI_Workspace-Blueprint pipeline
(`dev/active/self-ai-usage-plugin/`) until the project stabilizes; see `01-planning/spec.md` there for
the full design, decisions, and acceptance criteria this README summarizes.

## What it shows

- **Claude tab** — session (5h) and weekly (7d) rate-limit percent used and reset times, today's
  prompt/session/token counts, and a per-model token breakdown. Sourced from Omarchy's own
  `omarchy-agent-usage-claude` collector.
- **Copilot tab** — plan name, quota reset date, a credits-by-day chart, and today's credits used.
  Sourced from `bin/ai-usage-copilot`, which calls GitHub's private
  `api.github.com/copilot_internal/user` endpoint for the `bobbyw_jsi` account. That endpoint only ever
  reports a cumulative total for the current billing period, so the script samples it on every refresh
  into a local cache (`$XDG_CACHE_HOME/bwright.ai-usage/copilot-history.json` — dates and integer
  credit totals only, never the token) and derives the daily deltas from that history.

`h`/`l` or click switches tabs; `r`/Enter refreshes; Esc closes. Refreshes automatically every 15
minutes.

## Install

The live plugin directory (`~/.config/omarchy/plugins/bwright.ai-usage`) is a symlink to this repo, so
edits here are simultaneously git-tracked and hot-reloaded by `omarchy-shell`:

```
ln -s /mnt/repos/AI_UsagePlugin ~/.config/omarchy/plugins/bwright.ai-usage
```

Add `{ "id": "bwright.ai-usage" }` to the bar's `right` layout in `~/.config/omarchy/shell.json`, then
`omarchy-restart-shell`.

## Security: the Copilot credential

`bin/ai-usage-copilot` reuses the GitHub CLI's own stored credential for the `bobbyw_jsi` account
(`gh auth token --hostname github.com --user bobbyw_jsi`) rather than requesting or storing a separate
one. What that means in practice:

- **Token type and scopes.** It's a GitHub OAuth token issued to `gh` CLI's own OAuth app (`gho_`
  prefix), currently scoped `gist`, `read:org`, `repo`, `workflow` on this machine. The token's value
  is never displayed, logged, or written to disk by this plugin — only `gh` itself holds it in its own
  credential store.
- **Permission limit — read this before relying on it.** `api.github.com/copilot_internal/user` is an
  undocumented, internal endpoint (the one VS Code's Copilot Chat extension calls) with no published
  scope requirement. It does **not** correspond to any of the OAuth scopes above — Copilot's own
  service appears to authenticate by token/session validity and org membership, not by checking a
  `copilot` scope that doesn't exist in the public model. **The minimum permission actually required
  for this one endpoint cannot be established from the public GitHub API surface.** This plugin
  therefore reuses the same full-scope credential `gh` already holds for git/workflow operations,
  not a narrower or usage-only one. Accept that broader-than-necessary exposure as the tradeoff for
  using this widget; there is currently no narrower credential available to request instead.
- **Nothing is escalated automatically.** The plugin never runs `gh auth refresh`, requests new scopes,
  or falls back to another account or an environment-variable token override
  (`GH_TOKEN`/`GITHUB_TOKEN`/`GH_ENTERPRISE_TOKEN`/`GITHUB_ENTERPRISE_TOKEN` are explicitly stripped
  from the child environment before `gh` runs).
- **Fixed destination.** The token is sent only in the `Authorization` header of an HTTPS GET to the
  literal `https://api.github.com/copilot_internal/user` URL, with certificate/hostname validation on
  and every redirect (301/302/303/307/308, same-host or not) rejected outright.
- **Unavailable, not broken.** If `bobbyw_jsi` is signed out of `gh`, the token is revoked, or the
  endpoint returns a non-200/malformed response, the Copilot tab shows "Copilot usage unavailable" —
  the Claude tab is unaffected, and nothing retries with a weaker credential or insecure transport.

See `dev/active/self-ai-usage-plugin/01-planning/spec.md`'s "Security" section for the complete list of
constraints (subprocess argv/env handling, no plugin-written token files, logging rules) and
`tests/test_ai_usage_copilot.py` for how each of those is verified.

## Repo layout

- `manifest.json` — plugin manifest.
- `Panel.qml` — bar icon + popup (tabs, refresh timer, IPC verbs via
  `omarchy-shell bwright.ai-usage <open|close|toggle|refresh>`).
- `ClaudeSource.qml`, `CopilotSource.qml` — process-backed data sources feeding `Panel.qml`.
- `bin/ai-usage-copilot` — the Copilot fetch script described above.
- `tests/test_ai_usage_copilot.py` — pytest suite for that script (`pytest tests/`).
- `assets/claude.svg` — Anthropic's Claude mark, copied verbatim from
  `/usr/share/omarchy/shell/plugins/agents/assets/claude.svg` (the same asset the built-in
  `omarchy.agents` widget ships).
- `assets/github.png` — a glow-style GitHub mark supplied by the user, used to represent GitHub
  Copilot.
