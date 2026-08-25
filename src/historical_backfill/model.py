from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AuthorityError(RuntimeError):
    """Fail-closed authority or provenance error."""


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1_048_576):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


@dataclass(frozen=True)
class SourceSpec:
    provider: str
    family: str
    asset: str
    instrument: str
    day: str
    url: str
    checksum_url: str | None
    timestamp_column: int
    sequence_column: int
    timestamp_unit: str

    def identity(self) -> str:
        return hashlib.sha256(canonical_bytes(self.__dict__)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value))
