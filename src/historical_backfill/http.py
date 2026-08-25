from __future__ import annotations

import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .model import AuthorityError

USER_AGENT = "historical-rich-information/1.0 (+https://github.com/atalaydor)"
DELAYS = (0, 2, 8, 32)


@dataclass(frozen=True)
class Receipt:
    url: str
    etag: str | None
    last_modified: str | None
    content_length: int | None


def download(url: str, target: Path, maximum_bytes: int) -> Receipt:
    last: Exception | None = None
    for delay in DELAYS:
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                length_raw = response.headers.get("Content-Length")
                length = int(length_raw) if length_raw is not None else None
                if length is not None and length > maximum_bytes:
                    raise AuthorityError(f"source object exceeds byte breaker: {length}")
                written = 0
                with target.open("wb") as handle:
                    while chunk := response.read(1_048_576):
                        written += len(chunk)
                        if written > maximum_bytes:
                            raise AuthorityError("source object crossed byte breaker")
                        handle.write(chunk)
                return Receipt(
                    url, response.headers.get("ETag"), response.headers.get("Last-Modified"), length
                )
        except urllib.error.HTTPError as exc:
            target.unlink(missing_ok=True)
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise AuthorityError(f"source HTTP {exc.code}: {url}") from exc
            last = exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            target.unlink(missing_ok=True)
            last = exc
    raise AuthorityError(f"bounded source retry exhausted: {url}") from last


def get_text(url: str, maximum_bytes: int = 4096) -> tuple[str, Receipt]:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as temp:
        path = Path(temp) / "body"
        receipt = download(url, path, maximum_bytes)
        return path.read_text(encoding="utf-8"), receipt


def cleanup_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
