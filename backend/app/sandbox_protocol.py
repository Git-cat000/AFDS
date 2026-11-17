"""Stdlib-only wire limits shared by the trusted image and its host controller."""

import hashlib
import json
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TypedDict

PROTOCOL_VERSION = "afds-container-v1"
CAPTURE_LIMIT = 64 * 1024
LOG_LIMIT = 1024 * 1024
MAX_FILES = 32
MAX_FILE_BYTES = 8 * 1024**2
MAX_OUTPUT_BYTES = 32 * 1024**2
MAX_HEADER_BYTES = 512 * 1024
MAX_WIRE_BYTES = 34 * 1024**2


class ProtocolError(ValueError):
    pass


class SandboxResult(TypedDict):
    exit_code: int
    stdout: str
    stderr: str
    logs_truncated: bool
    error: dict | None
    actual_policy: dict
    artifacts: list[dict]
    output_dir: Path


def output_name(value):
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ProtocolError("Invalid output archive name")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or any(char in value for char in ':<>"|?*')
        or value == "."
        or any(ord(char) < 32 for char in value)
        or value != path.as_posix()
    ):
        raise ProtocolError("Invalid output archive path")
    for part in path.parts:
        if (
            part in {".", ".."}
            or part.endswith((".", " "))
            or re.fullmatch(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", part, re.I)
        ):
            raise ProtocolError("Invalid output archive path component")
    return value


def output_inventory(root):
    root = Path(root)
    result, entries, total, seen = [], 0, 0, set()
    for path in sorted(root.rglob("*")):
        entries += 1
        if entries > 256:
            raise ProtocolError("Output archive exceeds256entries")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ProtocolError("Output archive requires regular files")
        name = output_name(path.relative_to(root).as_posix())
        if name.casefold() in seen:
            raise ProtocolError("Output archive paths collide")
        seen.add(name.casefold())
        if stat.S_ISDIR(info.st_mode):
            continue
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_FILE_BYTES
            or len(result) >= MAX_FILES
        ):
            raise ProtocolError("Output archive exceeds file limits")
        total += info.st_size
        if total > MAX_OUTPUT_BYTES:
            raise ProtocolError("Output archive exceeds32MiB")
        with path.open("rb") as handle:
            sha = hashlib.file_digest(handle, "sha256").hexdigest()
        result.append({"name": name, "size": info.st_size, "sha256": sha})
    return result


def write_header(stream, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_HEADER_BYTES:
        raise ProtocolError("Sandbox header exceeds its limit")
    stream.write(data)
    stream.flush()


class LimitedReader:
    def __init__(self, stream, limit=MAX_WIRE_BYTES):
        self.stream, self.remaining = stream, limit

    def read(self, size=-1):
        requested = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        data = self.stream.read(requested)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ProtocolError("Sandbox output archive exceeds its wire limit")
        return data


def extract_archive(stream, destination, inventory):
    if len(inventory) > MAX_FILES:
        raise ProtocolError("Output archive has too many files")
    expected = {}
    for item in inventory:
        name = output_name(item["name"])
        size, sha = item["size"], item["sha256"]
        if (
            type(size) is not int
            or not 0 <= size <= MAX_FILE_BYTES
            or not re.fullmatch(r"[a-f0-9]{64}", sha)
            or name.casefold() in expected
        ):
            raise ProtocolError("Output archive inventory is invalid")
        expected[name.casefold()] = item
    if sum(item["size"] for item in expected.values()) > MAX_OUTPUT_BYTES:
        raise ProtocolError("Output archive inventory exceeds32MiB")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    found = set()
    with tarfile.open(fileobj=LimitedReader(stream), mode="r|") as archive:
        for member in archive:
            name = output_name(member.name)
            item = expected.get(name.casefold())
            if (
                not member.isreg()
                or member.issym()
                or member.islnk()
                or member.size > MAX_FILE_BYTES
                or item is None
                or name != item["name"]
                or name.casefold() in found
                or member.size != item["size"]
            ):
                raise ProtocolError("Output archive member is invalid")
            path = (destination / name).resolve()
            if not path.is_relative_to(destination):
                raise ProtocolError("Output archive escapes its destination")
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            source = archive.extractfile(member)
            if source is None:
                raise ProtocolError("Output archive member is unreadable")
            with source, path.open("xb") as writer:
                remaining = member.size
                while remaining:
                    chunk = source.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise ProtocolError("Output archive member is incomplete")
                    digest.update(chunk)
                    writer.write(chunk)
                    remaining -= len(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise ProtocolError("Output archive member hash differs")
            found.add(name.casefold())
    if found != set(expected):
        raise ProtocolError("Output archive is missing declared files")
    return inventory
