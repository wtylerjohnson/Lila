"""Strict JSON and portable, confined byte manifests."""
import hashlib
import json
from pathlib import Path, PurePosixPath

from .legacy.v3.discovery_score import nonempty, neutral, require, stamp


def loads(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("Nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def read(path):
    return loads(Path(path).read_text(encoding="utf-8"))


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha_bytes(Path(path).read_bytes())


def object_hash(value):
    return sha_bytes(encoded(value))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encoded(value))


def safe_path(base, name):
    nonempty(name, "relative path")
    require("\\" not in name and "\x00" not in name, "Invalid path separator")
    parts = name.split("/")
    require(not PurePosixPath(name).is_absolute() and
            all(p not in ("", ".", "..") for p in parts), "Unsafe relative path")
    base = Path(base)
    require(not any(p.is_symlink() for p in (base, *base.parents)), "Symlink root")
    path = base
    for part in parts:
        path = path / part
        require(not path.is_symlink(), "Symlink component")
    require(path.resolve().is_relative_to(base.resolve()), "Path escape")
    return path


def bound(base, name, expected):
    path = safe_path(base, name)
    require(path.is_file() and digest(path) == expected, "Changed or missing bound file: " + name)
    return path


def verify_manifest(base, manifest, schema):
    require(manifest["schema"] == schema, "Wrong schema; explicit migration required")
    require(isinstance(manifest["files"], dict) and manifest["files"], "Empty manifest")
    return {name: bound(base, name, sha) for name, sha in manifest["files"].items()}


def code_hash():
    root = Path(__file__).parent
    return object_hash({p.relative_to(root).as_posix(): digest(p)
                        for p in sorted(root.rglob("*.py"))})


def external_file(path, *untrusted_roots):
    path = Path(path)
    require(not any(p.is_symlink() for p in (path, *path.parents)), "Symlink trust file")
    require(not any(path.resolve().is_relative_to(Path(root).resolve())
                    for root in untrusted_roots), "Trust must live outside case and packet folders")
    return path
