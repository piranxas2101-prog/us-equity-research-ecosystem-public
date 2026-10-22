"""Read-only HTTP access to the configured research sources, owned by the project.

Babis used to load these helpers at runtime from a Codex skill folder in the
user's home directory (``~/.codex/skills/use-market-sources/scripts``). That
made the daily registry work depend on a file outside the repository. The two
functions Babis uses are kept here verbatim, so behaviour is unchanged:

- ``load_env`` reads provider settings from a ``.env`` file without printing them;
- ``fetch_json`` performs one GET and reports a status instead of raising.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error, request

TIMEOUT_SECONDS = 15


def load_env(path_arg: str | None) -> tuple[Path, dict[str, str]]:
    candidates: list[Path] = []
    if path_arg:
        candidates.append(Path(path_arg).expanduser())
    current = Path.cwd().resolve()
    candidates.extend(parent / ".env" for parent in (current, *current.parents))
    env_path = next((item.resolve() for item in candidates if item.is_file()), None)
    if env_path is None:
        raise SystemExit("No .env file found. Pass --env-file with a workspace-local path.")

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name.strip()] = value
    return env_path, values


def status_name(http_status: int) -> str:
    if http_status == 401:
        return "auth_failed"
    if http_status == 403:
        return "forbidden_or_plan"
    if http_status == 429:
        return "rate_limited"
    return "http_error"


def fetch_json(
    provider: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    safe_headers = {"Accept": "application/json", "User-Agent": "market-source-router/1.0"}
    safe_headers.update(headers or {})
    req = request.Request(url, headers=safe_headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {
                "provider": provider,
                "status": "ok",
                "http_status": int(response.status),
                "data": payload,
            }
    except error.HTTPError as exc:
        return {"provider": provider, "status": status_name(int(exc.code)), "http_status": int(exc.code)}
    except (error.URLError, TimeoutError):
        return {"provider": provider, "status": "timeout_or_network"}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"provider": provider, "status": "invalid_json"}
    except Exception:
        return {"provider": provider, "status": "unexpected_error"}
