#!/usr/bin/env python3
"""Enforce bounded direct dependencies and checked-in lock coverage."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LOCKFILE = ROOT / "uv.lock"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
CONSTRAINT_RE = re.compile(r"(?:===|==|~=|>=|<=|!=|>|<)")


def normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def dependency_name(spec: str) -> str:
    match = NAME_RE.match(spec)
    if match is None:
        raise ValueError(f"invalid dependency specification: {spec!r}")
    return match.group(0)


def main() -> int:
    project = tomllib.loads(PYPROJECT.read_text())
    lock = tomllib.loads(LOCKFILE.read_text())
    specs = list(project["project"]["dependencies"])
    for group in project.get("dependency-groups", {}).values():
        specs.extend(group)

    locked = {
        normalized_name(package["name"]) for package in lock.get("package", []) if "name" in package
    }
    violations: list[str] = []
    names: list[str] = []
    for spec in specs:
        name = dependency_name(spec)
        names.append(name)
        if "*" in spec or CONSTRAINT_RE.search(spec) is None:
            violations.append(f"{name}: missing bounded version constraint")
        elif "<" not in spec and "==" not in spec and "===" not in spec:
            violations.append(f"{name}: lower bound has no upper bound")
        if normalized_name(name) not in locked:
            violations.append(f"{name}: missing from uv.lock")

    result = {
        "status": "fail" if violations else "pass",
        "policy": "direct dependencies are bounded and present in uv.lock",
        "dependencies": sorted(names, key=normalized_name),
        "lock_package_count": len(locked),
        "violations": violations,
    }
    print(json.dumps(result, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
