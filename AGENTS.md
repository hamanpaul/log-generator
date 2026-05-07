<!-- managed-by: hamanpaul/paulsha-conventions@v1.0.0 -->
<!-- If this file changes, keep CLAUDE.md / AGENTS.md / GEMINI.md / .github/copilot-instructions.md synchronized. -->
policy_version: 1.0.0

# Agent Policy Checklist

This repository is governed by hamanpaul project policy v1.0.0.
All agents entering a session must follow this checklist.

## Repository Profile
- policy_profile: `flat` (see `.paul-project.yml`)
- policy_version: `1.0.0`

## Before Work
- [ ] Confirm the current branch is not `main`.
- [ ] If currently on `main`, create a `feature/<slug>` branch first.
- [ ] If this task spans multiple independent subtasks, recommend splitting
  work with `git worktree`.

## When Changing Code
- [ ] Update `CHANGELOG.md` under `[Unreleased]` in the same PR.
- [ ] Do not skip CHANGELOG unless the change is clearly docs-only, test-only,
  or chore-only.
- [ ] Paths listed in `.paul-project.yml` `code_paths` are treated as code
  changes.

## When Changing Version
- [ ] Follow `<MAJOR>.<MINOR>.<PATCH>[-fix.N]`.
- [ ] PATCH bump for `flat` corresponds to a completed feature batch.
- [ ] MINOR bump requires the planned feature set to be landed and seven days
  without hotfixes.
- [ ] MAJOR bump requires explicit user approval.

## Before Claiming Done
- [ ] `CHANGELOG.md [Unreleased]` has a matching entry, or the PR carries
  `skip-changelog` with a reason.
- [ ] `VERSION` matches the intended release state.
- [ ] `.github/pull_request_template.md` checklist is complete when present.
- [ ] Tests pass for the changed surface.
- [ ] `python3 -m policy_check --repo .` has no failures.
- [ ] Any skipped check has an allowed exemption label and reason.

## Prohibited
- Do not commit directly to `main` after the initial repository bootstrap.
- Do not create branches outside `feature/<slug>` or `wt/<feature>/<subtask>`.
- Do not invent new `policy-exempt:*` labels.
- Do not change this file without synchronizing the other three agent files.

## Exemption Label Allowlist
- `policy-exempt:readme-sections`
- `policy-exempt:changelog-format`
- `policy-exempt:pr-title`
- `policy-exempt:branch-name`
- `policy-exempt:agent-files`
- `policy-exempt:cli-help`
- `skip-changelog`
- `wip`
