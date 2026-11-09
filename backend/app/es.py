"""A small Elasticsearch client over the standard library.

Connection comes from the environment:
    ES_URL      default http://127.0.0.1:9200 (falls back to ES_LOCAL_URL)
    ES_API_KEY  (falls back to ES_LOCAL_API_KEY, the name start-local uses)
The key is only ever sent as a header. It is never logged or included in errors.
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:9200"


def loopback(url: str) -> str:
    """Use 127.0.0.1 for localhost. On Windows "localhost" tries IPv6 (::1) first, and start-local's
    Elasticsearch listens only on 127.0.0.1, so every request waited about 2 s for the fallback."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname != "localhost":
        return url
    netloc = parts.netloc.replace("localhost", "127.0.0.1", 1)
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


class ESError(Exception):
    """Elasticsearch could not be reached or returned an error."""


class ES:
    def __init__(self, url: str, api_key: str | None = None, timeout: float = 10):
        self.url = loopback(url).rstrip("/")
        self.timeout = timeout
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"ApiKey {api_key}"

    @classmethod
    def from_env(cls) -> "ES":
        return cls(os.environ.get("ES_URL") or os.environ.get("ES_LOCAL_URL") or DEFAULT_URL,
                   os.environ.get("ES_API_KEY") or os.environ.get("ES_LOCAL_API_KEY"))

    def request(self, method: str, path: str, body: dict | str | None = None, ndjson: bool = False,
                ok404: bool = False, timeout: float | None = None) -> dict | None:
        """Send any request. Used by the loaders; returns None for a 404 when ok404 is set."""
        headers = dict(self._headers)
        if ndjson:
            headers["Content-Type"] = "application/x-ndjson"
        data = None if body is None else (body if isinstance(body, str) else json.dumps(body)).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if ok404 and e.code == 404:
                return None
            raise ESError(f"{method} {path} returned HTTP {e.code}: {e.read().decode()[:500]}") from None
        except (OSError, http.client.HTTPException) as e:  # URLError, timeouts, dropped or cut-off responses
            raise ESError(f"could not reach Elasticsearch at {self.url}: {e}") from None
        except ValueError:
            raise ESError(f"{method} {path} returned a response that is not JSON") from None

    def search(self, index: str, body: dict) -> dict:
        request = urllib.request.Request(f"{self.url}/{index}/_search", data=json.dumps(body).encode(),
                                         method="POST", headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ESError(f"search on {index} returned HTTP {e.code}: {e.read().decode()[:500]}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            # URLError only wraps errors from sending the request. A dropped connection or a
            # short read while receiving the response raises OSError or HTTPException instead.
            # TimeoutError is an OSError.
            raise ESError(f"could not reach Elasticsearch at {self.url}: {e}") from None
        except ValueError:
            raise ESError(f"search on {index} returned a response that is not JSON") from None
