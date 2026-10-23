"""Read pip-compile pins, including extras, for installation and distribution."""
from dataclasses import dataclass
import re

PIN = re.compile(r"(?m)^([A-Za-z0-9_.-]+)(?:\[[^\]\r\n]+\])?==([^\s\\;]+)")


@dataclass(frozen=True)
class LockedDependency:
    name: str
    version: str
    hashes: frozenset[str]


def parse_dependency_lock(text: str) -> list[LockedDependency]:
    for line in text.splitlines():
        if line and not line.startswith((" ", "\t", "#", "--")) and not PIN.match(line):
            raise ValueError(f"Unsupported lock entry: {line.split()[0]}")
    matches = list(PIN.finditer(text))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        hashes = frozenset(re.findall(r"--hash=sha256:([0-9a-f]{64})", text[match.end():end]))
        result.append(LockedDependency(match[1], match[2], hashes))
    return result
