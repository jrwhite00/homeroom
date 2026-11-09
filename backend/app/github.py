"""A small GitHub REST client over the standard library.

The token comes from GITHUB_TOKEN (in the cluster, from a Kubernetes secret). It is only ever sent as
a header: never logged, printed, or included in an error message.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = "https://api.github.com"


class GitHubError(Exception):
    """The GitHub API could not be reached or returned an error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHub:
    def __init__(self, repo: str, token: str | None, api: str = API, timeout: float = 15):
        self.repo, self.api, self.timeout = repo, api.rstrip("/"), timeout
        self._token = token

    @classmethod
    def from_env(cls) -> "GitHub":
        return cls(os.environ.get("GITHUB_REPO", "jrwhite00/homeroom"), os.environ.get("GITHUB_TOKEN"))

    def request(self, method: str, path: str, body: dict | None = None) -> dict | list:
        if not self._token:
            raise GitHubError("no GitHub token is configured")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "Authorization": f"Bearer {self._token}", "User-Agent": "homeroom"}
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        url = f"{self.api}/repos/{self.repo}{path}"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise GitHubError(f"GitHub {method} {path} returned HTTP {e.code}: {detail}", e.code) from None
        except (OSError, ValueError) as e:
            raise GitHubError(f"could not reach GitHub: {type(e).__name__}") from None
