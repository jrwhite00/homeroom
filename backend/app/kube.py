"""kubectl, pinned to the kind-cnh context. Refuses any other current context."""
from __future__ import annotations

import json
import subprocess

CONTEXT = "kind-cnh"


class KubeError(RuntimeError):
    """kubectl failed."""


class Kubectl:
    def __init__(self, context: str = CONTEXT):
        self.context = context

    def current_context(self) -> str:
        out = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True)
        return out.stdout.strip()

    def check_context(self) -> None:
        if (ctx := self.current_context()) != self.context:
            raise KubeError(f"current kubectl context is '{ctx}', not {self.context}; refusing to touch it")

    def run(self, *args: str, stdin: str | None = None, ok_missing: bool = False) -> str:
        result = subprocess.run(["kubectl", "--context", self.context, *args], input=stdin,
                                capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            if ok_missing and "NotFound" in result.stderr:
                return ""
            raise KubeError(f"kubectl {' '.join(args[:2])} failed: {result.stderr.strip()[:400]}")
        return result.stdout

    def json(self, *args: str) -> dict:
        return json.loads(self.run(*args, "-o", "json"))
