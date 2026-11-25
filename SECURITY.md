# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through [GitHub's private vulnerability reporting](https://github.com/AndreaZavatta/agy-plan/security/advisories/new) instead, with the steps to reproduce it and the `agy` version you used (`agy --version`).

You can expect a first reply within a week. Once a fix is released, the advisory is published with credit to you, unless you prefer otherwise.

## Scope

agy-plan sends your repository to a Google model through the Antigravity CLI and relies on `agy`'s permission rules to keep that session read-only. Reports are in scope when agy-plan itself weakens those protections, for example:

- a way for Gemini to write files, run commands or use MCP tools despite the deny rules the script passes;
- a secrets file that the script should block but does not, such as a naming variant it misses;
- the skill leading Claude to act on a plan without the user's approval.

Prompt injection through repository content, and data sent through the network tools that stay available, are known limitations described in the [README](README.md#how-read-only-is-enforced). Bugs in `agy` itself belong to Google.
