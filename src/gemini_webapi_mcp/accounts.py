"""Safe multi-account policy and status storage for an OAuth gateway."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class AccountPolicy:
    name: str
    scopes: frozenset[str]
    enabled: bool = True
    requests_per_minute: int = 30

class AccountRegistry:
    """Resolve validated OAuth claims to accounts and enforce simple limits."""
    def __init__(self, config_path: str | Path, status_path: str | Path | None = None):
        self.config_path = Path(config_path)
        self.status_path = Path(status_path) if status_path else self.config_path.with_suffix(".status.json")
        self._config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self._windows: dict[str, list[float]] = {}

    def _policies(self) -> dict[str, AccountPolicy]:
        return {name: AccountPolicy(name, frozenset(raw.get("scopes", [])), bool(raw.get("enabled", True)), int(raw.get("requests_per_minute", 30))) for name, raw in self._config.get("accounts", {}).items()}

    def select(self, subject: str, account: str | None = None) -> AccountPolicy:
        claims = self._config.get("subjects", {}).get(subject)
        if claims is None:
            raise PermissionError("OAuth subject is not authorized")
        allowed = set(claims if isinstance(claims, list) else [claims])
        selected = account or (next(iter(allowed)) if len(allowed) == 1 else None)
        if selected is None or selected not in allowed:
            raise PermissionError("explicit Gemini account selection is required")
        policy = self._policies().get(selected)
        if policy is None or not policy.enabled:
            raise PermissionError("Gemini account is unavailable")
        return policy

    def allow_request(self, policy: AccountPolicy, scope: str) -> bool:
        if scope not in policy.scopes:
            return False
        now = time.monotonic()
        window = [stamp for stamp in self._windows.get(policy.name, []) if now - stamp < 60]
        if len(window) >= policy.requests_per_minute:
            self._windows[policy.name] = window
            return False
        self._windows[policy.name] = window + [now]
        return True

    def write_status(self, account: str, state: str, error: str | None = None) -> None:
        data = json.loads(self.status_path.read_text(encoding="utf-8")) if self.status_path.exists() else {}
        safe_error = error if error in {None, "TimeoutError", "login_required"} else type(error).__name__
        data[account] = {"state": state, "error": safe_error}
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
