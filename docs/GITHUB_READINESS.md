# GitHub readiness

The repository is structured for public review while local credentials and generated research stay on the user's machine.

## Completed

- The root README explains the purpose, architecture, agents, current verified run and safe quick start.
- Setup, advanced usage, contribution and security instructions have separate entry points.
- Historical run notes and old planning documents are retained under `docs/archive/` instead of competing with current guidance.
- `.gitignore` excludes credentials, databases, caches, logs, virtual environments, trained runtime state and generated output.
- `.env.example` provides empty configuration fields without publishing real secrets.
- CI compiles the core modules, verifies the ecosystem contract, runs tests and audits pinned dependencies and workflows.
- Public wording consistently describes a research and paper-trading toolkit, without performance promises or personalized advice.

## Publication boundaries

- Users bring their own data-provider credentials and paper-broker account.
- Live trading and third-party account custody remain outside the public workflow.
- Generated reports are local by default. A deliberately sanitized example may be added later if it contains no credentials, account data or licensed provider payloads.
- `education/` and `strategy_library/` are public project material and must follow the same evidence and review rules as source code.

## Owner decisions still open

1. **GitHub security settings:** enable private vulnerability reporting and branch protection in repository settings when ready for outside contributors.
2. **Public examples:** publish only small, sanitized example outputs after checking source redistribution terms.

The source is published under the [MIT License](../LICENSE), and the repository uses the descriptive name `us-equity-research-ecosystem`.

Before each push, run the unit tests, inspect `git diff`, and confirm that no `.env`, token, database or generated output has entered the index.
