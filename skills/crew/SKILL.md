---
name: crew
description: Configure agent roles with a resumable, preview-first setup wizard, manage role prompts and defaults, migrate RolePilot configuration, or diagnose Crew hooks.
---

# Crew

Use the bundled CLI at `bin/crew` relative to this plugin root, with Python 3.11+. All noninteractive commands return JSON; inspect exit codes. Never start another Codex or Claude Code process to use this tool.

Codex and Claude Code keep separate homes, roles and task stores. The host is chosen by `--host codex|claude`, then `CREW_HOST`, then the detected environment, then Codex. Pass `--host` explicitly whenever the answer must be unambiguous. A role defined for one host is invisible to the other; `hosts.claude` inside a role carries Claude Code's model and effort, while the top-level fields belong to Codex. Presets copy the other host's description and prompt once when a role is first created for this host.

## First-use and onboarding

1. Read `crew preflight --project <root> --json`. If `legacy_config_detected` is true, offer `crew migrate` preview before creating new configuration. Explain conflicting Crew values are preserved and old plugins/exports require cleanup; see README.
2. Run `crew init --project <root> --json`. It persists a draft and returns `revision` and a single `step` with a question, type and choices. It does not write live roles yet.
3. Present that step in the user's language. Use the host's native question/choice tool **if available**; otherwise present numbered textual choices and wait for the user's answer. This skill cannot force a native selector to appear. Do not silently choose roles, models or effort for the user.
4. For model selection, use actual host-supported model IDs if you can read them, or ask the user to provide them. `--models-file` accepts a JSON array to seed choices. Existing configured model IDs are suggestions, not proof of availability. Effort choices are syntactically allowed values; verify support in the host picker.
5. Submit the answer via `crew init --step <returned-id> --revision <returned-revision> --answer '<JSON value>'`. Answer types: roles = array of IDs; model and effort = string. Use safe argument quoting or a subprocess argument array. Inspect the next returned step; never guess revision or stage.
6. At `confirm`, show the entire relevant preview: additions with model, effort and prompt, preserved/skipped roles and target path. Wait for explicit user confirmation. Then submit JSON true plus `--apply --confirm-token <preview-token>`. A false answer leaves the draft pending. A changed configuration invalidates the preview; reread `init` and obtain approval for materially changed details.
7. A completed wizard only saves role configuration. Preview `crew export` and apply under the user's setup authorization. On Codex, separately preview/apply `crew setup` if the user wants a global AGENTS entry; on Claude Code that command reports `not_applicable` because the SessionStart hook already carries the status. Respect enabled=false; initialization preserves that choice.

Pause safely by stopping. Run init again to resume. `--reset --apply` starts a new draft and preserves existing live configuration. Completed state persists. Use `crew init --interactive` only in a real human terminal; never open TUI inside a Hook.

## Role management

Five bundled templates: coordinator, implementer, reviewer, researcher, tester. View full prompts with `presets list` or saved `role show NAME`. `presets install [names...] --model ID --effort LEVEL` imports missing templates only. For custom roles use `role create`; edit with `role update --instructions-file FILE` or `--instructions TEXT`. Omitted/empty instructions create an explicit empty_agent. Preserve an intentionally empty prompt rather than inventing a replacement. `role enable` restores saved definitions or removes inherited-role masks. `--effort` values differ per host: Codex accepts minimal/low/medium/high/xhigh, Claude Code accepts low/medium/high/xhigh/max.

Use `--lang zh|en` or CREW_LANG for interface language; template imports also accept `--prompt-lang`. Never translate stored user prompts just because the interface language changes.

## Diagnostics and limits

`preflight` reports stale/missing exports for the selected host. `enabled=false` plus `needs_export` means stale project exports need removal, not permission to re-enable the project. `doctor` distinguishes static checks and model availability not live verified. `hook ping` is a simulated test, not proof a host Hook fired. Read receipts with `hook status`. A native host plugin and Hook trust must be configured in the host.

The portable manifest's extensions.com.openai is authoritative for Codex; `.claude-plugin/plugin.json` is the Claude Code manifest. Codex loads `hooks/codex.json` through its manifest; Claude Code always loads `hooks/hooks.json`. Each hook event ends with exit code 0 even on failure, because exit code 2 would block the user's prompt.

No model calls, credential collection, spending or current-controller changes are performed by Crew. Preserve LICENSE when redistributing.

## Shared task context

When a Crew hook provides task_id and version, use that task record. Without a hook, run `crew context open --provider <host> --session <actual-session-identity>`; the provider defaults to the selected host. Pass `--task <known-id>` to share another role's task deliberately. Never invent an unrelated real host session identity, and never present another host's task ID as this host's: each host has its own store, and a task ID from the other host will not be found here. The store uses repository scope and exact provider/session association. Host hook session_id can cover a session tree/fork; it is not a verified AppServer threadId.

Read the full task state once with `context read --task ID`, and read global/project `context decisions`. Retain returned task_id/version and decision digests locally. On later turns read `context read --task ID --since LAST_SEEN_VERSION` only when version changes. Apply `changes` and `removed` to the local cache. Never advance your known version beyond a successfully read response. Refresh scoped decisions when their digest changes. A new agent starts with a full read; it cannot inherit another agent's in-memory version cursor.

After a meaningful decision, validated result, scope change, blocker, or handoff, write a concise `context patch` with expected-version and a unique request-id. State fields: summary, goal, constraints, decisions, progress, next, blockers, evidence. Use short strings/lists. Evidence should point to files/tests/commits, not copy logs. Distinguish completed and verified work from plans. Do not store secrets, transcripts or unsupported inferences. Use `checkpoint` when reorganizing a complete compact state; omit a field only when its old value is intentionally removed. CAS conflicts require rereading and merging, never blindly overwriting a newer version.

Store explicit cross-project user decisions via global decision-set; project-level durable decisions via project decision-set. A task record does not replace these scopes. Do not promote a temporary preference to a global rule without the user's direction.

If task_id changes or the hook reports reset, discard the old shared-task cache. A manually deleted task must not be reconstructed from the earlier transcript or old checkpoint. Work from the current request and newly established facts, asking for necessary missing information. Stale writes to the old ID are rejected. Explicit user-requested restore may recover an old record, but must not silently relink an active replacement session.

This release does not detect host archive/delete events or automatically clean up lifecycle records. Manual archive/delete/restore and session-state are available in CLI/TUI. SessionEnd/unknown does not mean deletion. A local tool cannot retract text already inside a model conversation or guarantee the model writes a checkpoint; follow this protocol when working with Crew.
