from __future__ import annotations

import http.client
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .http import DELAYS, USER_AGENT
from .model import AuthorityError, hash_file

MAX_ASSET_BYTES = 1_900_000_000


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    state: str
    api_url: str
    asset_id: int


class GitHubReleases:
    """Authenticated immutable staging with read-after-write reconciliation."""

    def __init__(self) -> None:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN", "")
        if not repository or not token:
            raise AuthorityError("GitHub authority environment is unavailable")
        self.repository = repository
        self.token = token
        self.api = f"https://api.github.com/repos/{repository}"

    def request(self, method: str, url: str, payload: bytes | None = None) -> Any:
        retryable = method in {"GET", "PATCH", "DELETE"}
        delays = DELAYS if retryable else (0,)
        last: Exception | None = None
        for delay in delays:
            if delay:
                time.sleep(delay)
            request = urllib.request.Request(
                url,
                data=payload,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    body = response.read()
                return json.loads(body) if body else None
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise AuthorityError(f"GitHub API {method} failed: {exc.code}") from exc
                last = exc
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                last = exc
        raise AuthorityError(f"GitHub API {method} exhausted retries") from last

    def releases(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 11):
            raw: list[dict[str, Any]] = self.request(
                "GET", f"{self.api}/releases?per_page=100&page={page}"
            )
            result.extend(raw)
            if len(raw) < 100:
                return result
        raise AuthorityError("release inventory exceeds bound")

    def ensure_draft(self, tag: str) -> int:
        matches = [item for item in self.releases() if item.get("tag_name") == tag]
        if matches:
            if len(matches) != 1 or not matches[0].get("draft"):
                raise AuthorityError(f"ambiguous or non-draft release: {tag}")
            return int(matches[0]["id"])
        payload = json.dumps(
            {"tag_name": tag, "name": tag, "draft": True, "prerelease": False}
        ).encode()
        try:
            created: dict[str, Any] = self.request("POST", f"{self.api}/releases", payload)
            release_id = int(created["id"])
        except AuthorityError:
            matches = [item for item in self.releases() if item.get("tag_name") == tag]
            if len(matches) != 1 or not matches[0].get("draft"):
                raise
            release_id = int(matches[0]["id"])
        return release_id

    def assets(self, release_id: int) -> list[Asset]:
        result: list[Asset] = []
        for page in range(1, 11):
            raw: list[dict[str, Any]] = self.request(
                "GET", f"{self.api}/releases/{release_id}/assets?per_page=100&page={page}"
            )
            result.extend(
                Asset(item["name"], int(item["size"]), item["state"], item["url"], int(item["id"]))
                for item in raw
            )
            if len(raw) < 100:
                return result
        raise AuthorityError("release asset inventory exceeds bound")

    def publish_directory(self, tag: str, partition: str, directory: Path) -> list[Asset]:
        release_id = self.ensure_draft(tag)
        existing = {item.name: item for item in self.assets(release_id)}
        accepted: list[Asset] = []
        prefix = partition.replace("/", "--")
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            size, digest = hash_file(path)
            if size >= MAX_ASSET_BYTES:
                raise AuthorityError("release asset exceeds cap")
            name = f"{prefix}--{digest}--{path.name}"
            logical_start = f"{prefix}--"
            logical_end = f"--{path.name}"
            divergent = [
                x
                for x in existing
                if x.startswith(logical_start) and x.endswith(logical_end) and x != name
            ]
            if divergent:
                raise AuthorityError(f"divergent durable output: {partition}/{path.name}")
            asset = existing.get(name)
            if asset is None:
                asset = self._upload(release_id, name, path)
            if asset.size != size or asset.state != "uploaded":
                raise AuthorityError("remote asset metadata mismatch")
            self._verify_download(asset, digest, size, directory)
            accepted.append(asset)
        return accepted

    def _upload(self, release_id: int, name: str, path: Path) -> Asset:
        query = urllib.parse.urlencode({"name": name})
        endpoint = f"/repos/{self.repository}/releases/{release_id}/assets?{query}"
        size = path.stat().st_size
        connection = http.client.HTTPSConnection("uploads.github.com", timeout=180)
        try:
            connection.putrequest("POST", endpoint)
            for key, value in {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            }.items():
                connection.putheader(key, value)
            connection.endheaders()
            with path.open("rb") as handle:
                while chunk := handle.read(1_048_576):
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read()
            if response.status not in {200, 201}:
                matches = [x for x in self.assets(release_id) if x.name == name]
                if len(matches) == 1:
                    return matches[0]
                raise AuthorityError(f"GitHub upload failed: {response.status}")
            raw = json.loads(body)
            return Asset(raw["name"], int(raw["size"]), raw["state"], raw["url"], int(raw["id"]))
        finally:
            connection.close()

    def _verify_download(self, asset: Asset, digest: str, size: int, directory: Path) -> None:
        target = directory / f".{asset.name}.verify"
        request = urllib.request.Request(
            asset.api_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/octet-stream",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with (
                urllib.request.urlopen(request, timeout=180) as response,
                target.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle, length=1_048_576)
            if hash_file(target) != (size, digest):
                raise AuthorityError("remote read-after-write verification failed")
        finally:
            target.unlink(missing_ok=True)

    def finalize(self, tag: str) -> None:
        matches = [item for item in self.releases() if item.get("tag_name") == tag]
        if len(matches) != 1 or not matches[0].get("draft"):
            raise AuthorityError("finalize requires exactly one draft")
        self.request(
            "PATCH",
            f"{self.api}/releases/{matches[0]['id']}",
            json.dumps({"draft": False}).encode(),
        )
