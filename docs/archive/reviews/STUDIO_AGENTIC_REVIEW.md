# Studio / VS Code Agentic Review

Reviewed on `2026-04-30`.

This note tracks which recent Studio / VS Code agentic capabilities are useful for this project. The goal is not to change the trading pipeline just because the editor changed. The goal is to use the editor better for auditability, GitHub work, background runs, and agent-review workflows.

## Sources Checked

- VS Code `1.115` release notes: https://code.visualstudio.com/updates/v1_115
- VS Code `1.116` release notes: https://code.visualstudio.com/updates/v1_116
- VS Code `1.117` release notes: https://code.visualstudio.com/updates/v1_117
- VS Code `1.118` release notes: https://code.visualstudio.com/updates/v1_118
- Visual Studio April 2026 cloud-agent update: https://devblogs.microsoft.com/visualstudio/visual-studio-april-update-cloud-agent-integration/
- GitHub Copilot in VS Code March releases: https://github.blog/changelog/2026-04-08-github-copilot-in-visual-studio-code-march-releases/

## Useful Now

- Agent Debug Logs: useful for understanding what happened in previous agent sessions and for reviewing failed or confusing agent behavior after the fact.
- Background terminal notifications and terminal interaction: useful for long-running scans, monitor scripts, and paper-trading processes that may need attention without manual polling.
- Semantic codebase search and GitHub text search: useful for repo cleanup, agent review, and finding exact pipeline behavior across scripts.
- Dedicated context for skills: useful when we create internal skills such as `broker-reconciliation`, `trader-review`, and `paper-trading-safety-rules`, because skill work should not pollute the main chat context.
- Chat Customizations Evaluation extension: useful later for checking our prompt, agent, instruction, and skill files before publishing or relying on them.
- Session insights / Chronicle: useful as an idea for our own audit trail, daily standup, and "what changed today" summaries.

## Test Later

- VS Code Agents app in Insiders: useful conceptually because it supports parallel agent sessions, diff review, and PR creation, but it is still preview/Insiders and should not become required for the pipeline yet.
- Remote control for Copilot CLI sessions: useful pattern for supervising long-running work remotely, but it is Copilot-specific and experimental.
- Cloud agents from Visual Studio: useful for repo-based PR work, but they depend on Copilot/GitHub issue and PR workflows. Keep this optional, not part of the trading runtime.
- BYOK in VS Code chat: useful only if we later use Copilot Business/Enterprise and want model-provider control inside VS Code.

## Ignore For Now

- C++ code-editing tools: not relevant to this Python/PowerShell pipeline.
- Copilot completion shortcuts and IntelliSense priority changes: nice editor quality-of-life items, but not relevant to our system design.
- Autopilot-style fully autonomous IDE sessions: not appropriate for broker actions. Our pipeline should keep broker execution behind explicit rules, ownership, reports, and audit artifacts.

## Decision For This Project

Do not change the trading logic because of the Studio update.

Use the update in three safe ways:

- Improve developer workflow: use better terminal/session visibility while scans and monitors run.
- Improve auditability: use agent debug/session history ideas to strengthen our own report and meeting artifacts.
- Improve future customization: design internal skills with isolated context and evaluation before we let any skill influence automation.

Broker actions remain controlled by the project pipeline:

- `yankos` and `periklis` decide trader intent.
- `koulis` tracks ownership and reminders.
- `thymios` executes only clear, pipeline-approved broker actions.
- 1v1 chats or agent meetings may recommend actions, but should not execute broker commands by default.

