# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

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
