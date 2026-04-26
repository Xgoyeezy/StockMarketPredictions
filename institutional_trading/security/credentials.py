from __future__ import annotations
import os
from dataclasses import dataclass
@dataclass(frozen=True)
class EnvironmentCredentialStore:
    prefix: str = "ITRADING_"
    def get(self, name: str) -> str | None: return os.environ.get(f"{self.prefix}{name.upper()}")
    def require(self, name: str) -> str:
        value = self.get(name)
        if not value: raise RuntimeError(f"Missing required credential environment variable: {self.prefix}{name.upper()}")
        return value
