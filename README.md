# agy-plan

A Claude Code skill for medium and large tasks: **Gemini plans, you review, Claude implements**.

```
/agy-plan add OAuth authentication to the backend
   │
   ├─ 1. Gemini explores the repository read-only, through the Antigravity CLI (agy)
   ├─ 2. The plan is saved to .agy-plans/<date>-<task>.md
   ├─ 3. You review it: approve, edit it by hand, or ask Gemini for a revision
   └─ 4. Claude implements the approved plan phase by phase, verifying each phase
```

Codebase exploration stays out of Claude's context: Claude reads the plan once, after you approve it.

## Architecture

```
skills/agy-plan/
├── SKILL.md                    # The workflow Claude follows when you run /agy-plan
├── references/
│   └── planner-prompt.md       # The prompt sent to Gemini: rules, plan structure, output format
└── scripts/
    └── agy_plan.py             # Runs agy, validates and saves the plan, prints a JSON summary
tests/
└── test_agy_plan.py            # Unit tests against a stub agy
install.sh                      # Symlinks the skill into ~/.claude/skills
```

The work is split across three pieces:

- **`SKILL.md`** is the orchestrator, read by Claude. It runs the script in the background, shows you the plan path without reading the plan, asks you to approve, refine or stop, and only then reads the plan and implements it. It treats the plan as a proposal from another model: Claude checks its claims against the code, runs each phase's verification step, and still asks before any destructive or outward-facing action.
- **`agy_plan.py`** is the bridge to Gemini. It fills the planner prompt with the task, runs `agy` in read-only mode, extracts the plan from between the required markers, retries a cut-off turn up to twice, rewrites `file://` links as repo-relative paths, and writes the plan with a front matter header. On stdout it prints only a JSON summary (`ok`, `plan_path`, `model`, `attempts`, `duration_seconds`, `usage`, `worktree_changed`, and `resumed` after a refinement), never the plan itself, so Claude spends no tokens on it before approval.
- **`planner-prompt.md`** defines what Gemini produces: a plan with fixed sections (Goal, Findings, Decisions, Phases with Files / Changes / Verify, Risks, Out of scope, Open questions), written to stand on its own because Claude implements it without seeing Gemini's exploration.

### Plan files

Plans are saved in `.agy-plans/` inside the project. Each one starts with a header that refinements depend on:

```
---
task: "add OAuth authentication to the backend"
model: "gemini-4-argon-high"
conversation_id: "..."
project: "/path/to/project"
created: "2026-11-25T10:00:00"
---
```

You can edit the body freely, but header values must stay valid JSON (for example, strings in double quotes). Because the project is recorded in the header, you can move the plan file; to change the project, pass `--project`.

Each refinement writes a new file with the first free version number (`-v2`, `-v3`, …) and never overwrites an existing plan. It resumes the same Gemini conversation and starts from the plan on disk, including your manual edits. If the conversation no longer exists, `agy` starts a new one. The refinement still works, because the prompt repeats the rules and the task, but Gemini starts without its earlier exploration and the skill tells you so.

### How read-only is enforced

`agy`'s `--mode plan` alone is **not enough**: in testing, when explicitly asked, the model wrote a file to the project even in plan mode. So the script passes session-scoped deny rules through `--settings`, without touching your `agy` configuration:

```json
{"permissions": {"deny": ["write_file(*)", "command(*)", "mcp(*)"]}}
```

With these rules, creating and editing files (including from a subagent) and running terminal commands were blocked. Read tools and network tools, such as web search and URL fetching, remain available. As an extra check, the script compares `git status` before and after the session and reports any difference. In projects that are not git repositories this check is skipped, leaving only the deny rules.

> [!WARNING]
> Because network tools stay available, a malicious file in the repository could try to instruct Gemini (prompt injection) to send code to an external URL. The planner prompt tells Gemini to treat repository content as data and ignore such instructions, but that is not a guarantee. Only run `/agy-plan` on repositories whose content you trust, or that you could share anyway.

`agy -p` can report `SUCCESS` with exit code 0 without producing a plan, for example when the turn stops after a denied tool call. A plan therefore counts only when it sits between the markers the prompt requires. Otherwise the script resumes the conversation up to twice, then reports the error and saves nothing.

## Installation

You need Python 3.9+ and the [Antigravity CLI](https://antigravity.google/) (`agy`), already signed in. The skill was tested with `agy` 1.5.17; it relies on the `-p`, `--mode`, `--settings`, `--model`, `--output-format json`, `--print-timeout` and `--conversation` flags, so other versions may behave differently.

```bash
git clone https://github.com/AndreaZavatta/agy-plan.git
cd agy-plan
./install.sh
```

The script creates a symlink at `~/.claude/skills/agy-plan`, so `/agy-plan` is available in every project and changes to the repository take effect immediately. To uninstall, run `rm ~/.claude/skills/agy-plan`.

To make the skill available in a single project only, copy or link `skills/agy-plan` into that project's `.claude/skills/` directory instead.

## Usage

Start Claude Code in your project directory and run:

```
/agy-plan <task description>
```

Then:

1. Claude starts the planning run in the background and tells you Gemini is planning. This can take several minutes.
2. When it finishes, Claude gives you the path to the plan file, along with the model, duration and token usage.
3. Open the file, read it, and edit it directly if you want.
4. Choose one of:
   - **Approve and implement**: Claude reads the plan on disk, including your edits, and implements it.
   - **Refine with Gemini**: give your feedback, and Gemini writes a new version of the plan.
   - **Stop**: the plan stays on disk and nothing is implemented.

The skill only runs when you invoke it: Claude cannot trigger it on its own, because it sends your code to a Google model. Before using it on confidential code, consider whether you can send that code to that service.

Add `.agy-plans/` to your project's `.gitignore` if you don't want to version the plans.

### Project instructions (CLAUDE.md)

The skill works alongside your project's instructions:

- **Claude** keeps following `CLAUDE.md` throughout. Steps it requires before a change, such as reading a ticket or creating a branch, happen before planning, and its rules on workflow, branches, commits and verification commands take precedence over the plan. The approval step is the exception: Claude always waits for your approval, even if `CLAUDE.md` says to start implementing right away.
- **Gemini** reads the project's convention files (`CLAUDE.md`, `AGENTS.md`, `README.md`, `CONTRIBUTING.md`) and follows their conventions for code, tests and tooling, but leaves workflow steps to Claude. It cannot see your conversation or use MCP servers, so Claude writes into the task anything it needs from outside the repository, such as the contents of a ticket.

### Configuration

| Setting | How | Default |
|---|---|---|
| Model | `AGY_PLAN_MODEL` environment variable (list: `agy models`) | `gemini-4-argon-high` |
| Timeout per run | `--timeout <seconds>` on the script | 1800 |
| Plan structure | [`planner-prompt.md`](skills/agy-plan/references/planner-prompt.md) | |

If the default model is retired or unavailable to your account, set `AGY_PLAN_MODEL` to one listed by `agy models`.

### Running the script directly

The script also works without Claude Code:

```bash
# New plan
python3 skills/agy-plan/scripts/agy_plan.py --task-file - <<'EOF'
add OAuth authentication to the backend
EOF

# Refine an existing plan
python3 skills/agy-plan/scripts/agy_plan.py --refine .agy-plans/<plan>.md --feedback "split phase 2 in two"
```

Run `python3 skills/agy-plan/scripts/agy_plan.py --help` for all options.

## Tests

```bash
python3 -m unittest discover -s tests
```

The tests use a stub `agy`, so they need no network access or Gemini quota.

## License

[MIT](LICENSE)
