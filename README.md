# AI Usage — an Omarchy bar widget

An [Omarchy](https://omarchy.org/) shell bar-widget plugin (`bob-wright8778.ai-usage`) that shows
Claude Code and GitHub Copilot usage from one bar icon and one popup panel, themed to match the
active Omarchy theme.

## What it shows

- **Claude tab** — session (5h) and weekly (7d) rate-limit percent used and reset times, today's
  prompt/session/token counts, and a per-model token breakdown. Sourced from Omarchy's own
  `omarchy-agent-usage-claude` collector.
- **Copilot tab** — plan name, quota reset date, total credits used for the current billing period, a
  credits-by-day chart, and today's credits used. Sourced from `bin/ai-usage-copilot`, which calls
  GitHub's private `api.github.com/copilot_internal/user` endpoint. That endpoint only ever reports a
  cumulative total for the current billing period, so the script samples it on every refresh into a
  local cache (`$XDG_CACHE_HOME/ai-usage/copilot-history[-LOGIN].json` — dates and integer credit
  totals only, never the token) and derives the daily deltas from that history.

`h`/`l` or click switches tabs; `r`/Enter refreshes; Esc closes. Refreshes automatically every 15
minutes.

## Requirements

- **Omarchy**, for the widget itself. `Panel.qml` inherits Omarchy's own `Panel` type and imports its
  `qs.Commons`/`qs.Ui` modules from `/usr/share/omarchy/shell/`, so it needs the Omarchy shell — not
  just upstream Quickshell. There is no build of this widget for other distros or other bars.
- **`omarchy-agent-usage-claude`**, shipped with Omarchy, for the Claude tab.
- **The [`gh`](https://cli.github.com/) CLI, signed in**, for the Copilot tab. Any Copilot plan that
  the `copilot_internal/user` endpoint reports on will work.

### Using the Copilot collector on its own

`bin/ai-usage-copilot` has no Omarchy dependency at all — it is stdlib-only Python 3 plus `gh`, so it
runs on any distro (and on macOS) independently of this widget:

```
./bin/ai-usage-copilot [--user LOGIN]
{"plan":"enterprise","quotaResetDate":"2026-11-01","recentDays":[...],"todayCreditsUsed":412,"totalCreditsUsed":11807}
```

Feed that JSON to waybar, polybar, tmux, a shell prompt, or anything else that can run a command. It
exits 0 with one JSON object on stdout, or 1 with a one-line credential-free diagnostic on stderr.

## Install

```
omarchy plugin add https://github.com/bob-wright8778/AI_UsagePlugin.git --enable
```

Plugins run as unsandboxed code inside your long-lived `omarchy-shell` process, so review the source
before enabling it — `omarchy plugin add` says the same thing, and it is worth taking literally.

To install it by hand instead, clone anywhere and symlink it into place (handy when you want to edit
it, since `omarchy-shell` hot-reloads anything under `~/.config/omarchy/plugins/`):

```
git clone https://github.com/bob-wright8778/AI_UsagePlugin.git
ln -s "$PWD/AI_UsagePlugin" ~/.config/omarchy/plugins/bob-wright8778.ai-usage
omarchy plugin enable bob-wright8778.ai-usage --section right
```

### Install from a tarball

If you were handed a `bob-wright8778.ai-usage-vX.Y.Z.tar.gz` instead of the repo URL, unpack it
straight into the plugins directory — the archive already contains a correctly named top-level
folder, so no renaming is needed:

```
tar -xzf bob-wright8778.ai-usage-v2.0.0.tar.gz -C ~/.config/omarchy/plugins/
omarchy plugin validate ~/.config/omarchy/plugins/bob-wright8778.ai-usage
omarchy plugin enable bob-wright8778.ai-usage --section right
```

A tarball install isn't git-managed, so `omarchy plugin update` won't see it — replace the folder
with a newer tarball to upgrade, or install from the repo URL instead if you want updates.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `githubUser` | `""` | The `gh` account login to read the Copilot quota for. Leave blank to use whichever account `gh` has active. |

Set it only if `gh` holds more than one account — a personal login and a work/enterprise login, say —
and you want the widget pinned to a specific one. In `~/.config/omarchy/shell.json`:

```json
{ "id": "bob-wright8778.ai-usage", "githubUser": "octocat" }
```

Each account gets its own history cache file, so switching accounts never diffs one account's
cumulative credit total against another's.

The refresh interval is fixed at 15 minutes and is not exposed as a setting.

## Security: the Copilot credential

`bin/ai-usage-copilot` reuses the GitHub CLI's own stored credential (`gh auth token --hostname
github.com`, plus `--user LOGIN` when `githubUser` is set) rather than requesting or storing a
separate one. What that means in practice:

- **Token type and scopes.** It's whatever OAuth token `gh` already holds for your account (typically
  a `gho_`/`ghu_` token issued to `gh`'s own OAuth app, with the scopes you granted `gh` for ordinary
  git and workflow operations). The token's value is never displayed, logged, or written to disk by
  this plugin — only `gh` itself holds it in its own credential store. Run `gh auth status` to see
  what that credential is scoped to on your machine.
- **Permission limit — read this before relying on it.** `api.github.com/copilot_internal/user` is an
  undocumented, internal endpoint (the one VS Code's Copilot Chat extension calls) with no published
  scope requirement. It does **not** correspond to any OAuth scope in the public model — Copilot's own
  service appears to authenticate by token/session validity and org membership, not by checking a
  `copilot` scope that doesn't exist. **The minimum permission actually required for this one endpoint
  cannot be established from the public GitHub API surface.** This plugin therefore reuses the same
  full-scope credential `gh` already holds, not a narrower or usage-only one. Accept that
  broader-than-necessary exposure as the tradeoff for using this widget; there is currently no
  narrower credential available to request instead.
- **Nothing is escalated automatically.** The plugin never runs `gh auth refresh`, requests new scopes,
  or falls back to another account or an environment-variable token override
  (`GH_TOKEN`/`GITHUB_TOKEN`/`GH_ENTERPRISE_TOKEN`/`GITHUB_ENTERPRISE_TOKEN` are explicitly stripped
  from the child environment before `gh` runs).
- **Fixed destination.** The token is sent only in the `Authorization` header of an HTTPS GET to the
  literal `https://api.github.com/copilot_internal/user` URL, with certificate/hostname validation on
  and every redirect (301/302/303/307/308, same-host or not) rejected outright.
- **Unavailable, not broken.** If the account is signed out of `gh`, the token is revoked, or the
  endpoint returns a non-200/malformed response, the Copilot tab shows "Copilot usage unavailable" —
  the Claude tab is unaffected, and nothing retries with a weaker credential or insecure transport.

`tests/test_ai_usage_copilot.py` verifies each of those: the pinned `gh` invocation (argv and
environment), redirect and TLS-certificate failures exercised through the real opener rather than a
stub, and that no token ever reaches stdout, stderr, a subprocess argument or environment, or the
local history cache.

## Repo layout

- `manifest.json` — plugin manifest, including the settings schema.
- `Panel.qml` — bar icon + popup (tabs, refresh timer, IPC verbs via
  `omarchy-shell bob-wright8778.ai-usage <open|close|toggle|refresh>`).
- `ClaudeSource.qml`, `CopilotSource.qml` — process-backed data sources feeding `Panel.qml`.
- `bin/ai-usage-copilot` — the Copilot fetch script described above.
- `tests/test_ai_usage_copilot.py` — pytest suite for that script (`pytest tests/`).
- `assets/claude.svg` — Anthropic's Claude mark, copied verbatim from
  `/usr/share/omarchy/shell/plugins/agents/assets/claude.svg` (the same asset the built-in
  `omarchy.agents` widget ships).
- `assets/github.png` — a glow-style GitHub mark, used to represent GitHub Copilot.

## License

MIT — see [LICENSE](LICENSE).
