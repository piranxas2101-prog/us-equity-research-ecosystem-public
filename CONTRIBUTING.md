# Contributing

Contributions should preserve the project's evidence-first and research-only boundaries.

## Before opening a change

1. Create an issue describing the observed problem and expected behavior.
2. Keep credentials and generated artifacts out of commits.
3. Do not change agent thresholds, risk settings or promotion gates without a documented rationale and focused validation.
4. Keep Babis, Giannis, Petros, the traders and Sokratis within their documented responsibilities.

## Validation

Use the locked local Python runtime:

```powershell
.\.venv-next\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Changes to PowerShell launchers must also pass the PowerShell parser. A full market run is not required for documentation-only changes.

## Pull requests

Describe the problem, resulting behavior, validation performed and any remaining limitations. Never include `.env`, API keys, local databases, provider payloads, logs, trained runtime models or generated research reports.
