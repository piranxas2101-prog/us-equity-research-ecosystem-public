# Security policy

## Reporting a vulnerability

Do not publish credentials, exploit details or sensitive provider responses in a public issue. Contact the repository owner privately through the security reporting channel available on the GitHub repository.

Include the affected component, reproduction conditions, practical impact and a minimal redacted example. Do not include real API keys or account identifiers.

## Supported scope

Security work currently covers the latest `main` branch. Local `.env` files, SQLite databases, caches, logs, models and generated outputs are intentionally excluded from Git.

The default pipeline is research-only. Paper execution requires an explicit command and separate Alpaca paper credentials. Live-money execution is outside the supported scope.
