# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-25

### Added

- Gemini is blocked from reading the project's secrets files (`.env`, `.env.*`, `.envrc`, `.npmrc`, `.pypirc`, `.netrc`, `*.pem`, `*.key`), with one exact `read_file` deny rule per file because agy ignores glob patterns in these rules. Templates such as `.env.example` stay readable, and the JSON summary lists the blocked files in `blocked_files`.
- `SECURITY.md`, explaining how to report a vulnerability, and a note in the README that the project is not affiliated with Google or Anthropic.

### Changed

- Project instructions such as `CLAUDE.md` take precedence over the plan on workflow, branches, commits, tickets and verification commands, and their preliminary steps run before planning.
- Claude commits as the project instructions say, instead of never committing unless asked; without such instructions the old behavior stays.
- The approval step stays mandatory even when project instructions say to start implementing right away.
- Claude writes context Gemini cannot reach, such as ticket contents from MCP servers, into the task.
- Gemini reads the project's convention files (`CLAUDE.md`, `AGENTS.md`, `README.md`, `CONTRIBUTING.md`) and uses their conventions and commands in the plan.

## [1.0.0] - 2026-11-25

### Added

- `/agy-plan` skill: Gemini plans through the Antigravity CLI (`agy`), the user reviews the plan, and Claude implements it phase by phase.
- Read-only planning sessions, enforced with session-scoped deny rules for file writes, terminal commands and MCP tools, plus a `git status` check before and after each session.
- Plans saved as Markdown in `.agy-plans/`, with a JSON front matter header (`task`, `model`, `conversation_id`, `project`, `created`).
- Plan refinement that resumes the original Gemini conversation, keeps the user's manual edits, and writes a new version (`-v2`, `-v3`, …) without overwriting existing plans.
- Automatic retries, up to two, when `agy` ends a turn without a complete plan.
- `AGY_PLAN_MODEL` environment variable and `--model`, `--project` and `--timeout` options.
- `install.sh`, which symlinks the skill into `~/.claude/skills`.
- Unit tests against a stub `agy`.
