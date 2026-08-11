from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_TEXT_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {
    ".conf", ".css", ".env", ".example", ".html", ".ini", ".java", ".js",
    ".json", ".jsx", ".md", ".mjs", ".properties", ".ps1", ".py", ".sh",
    ".sql", ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml",
}
TEXT_NAMES = {"Dockerfile", "Makefile", "pom.xml", ".gitignore"}
SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)$"
)
ASSIGNMENT = re.compile(
    r'''(?ix)
    ["']?([A-Z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key))["']?
    \s*[:=]\s*
    ["']?([^\s,"'\}\]]+)
    '''
)
CREDENTIAL_URI = re.compile(r"(?i)\b(?:postgres(?:ql)?|mongodb(?:\+srv)?|amqp)://[^:\s/]+:([^@\s/]+)@")
KNOWN_FORMATS = (
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("GITHUB_FINE_GRAINED_TOKEN", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("OPENAI_STYLE_KEY", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("PRIVATE_KEY_BLOCK", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
WEAK_OR_EXAMPLE = re.compile(
    r"(?i)^(?:apf|root|guest|minioadmin|admin123|password|secret|token|test|example|placeholder|"
    r"change[-_]?me(?:[-_]?in[-_]?production)?|dev[-_A-Za-z0-9]*|\*+|x+)$"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def is_literal_candidate(value: str) -> bool:
    stripped = value.strip().strip('"\'')
    lowered = stripped.lower()
    if not stripped or WEAK_OR_EXAMPLE.fullmatch(stripped):
        return False
    if stripped.startswith(("${", "$env:", "{{", "<")):
        return False
    if "(" in stripped or ")" in stripped or "=>" in stripped:
        return False
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+", stripped):
        return False
    if any(marker in lowered for marker in ("os.getenv", "process.env", "secrets.", "env.")):
        return False
    return True


def looks_like_high_entropy_secret(value: str) -> bool:
    normalized = value.strip().strip('"\'')
    if not is_literal_candidate(normalized) or len(normalized) < 24:
        return False
    classes = sum(
        bool(re.search(pattern, normalized))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[-_+=/.]")
    )
    return classes >= 3 and shannon_entropy(normalized) >= 3.5


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in KNOWN_FORMATS:
            if pattern.search(line) and (line_number, rule) not in seen:
                findings.append(Finding(path, line_number, rule, "credential-like literal must not be committed"))
                seen.add((line_number, rule))

        for match in ASSIGNMENT.finditer(line):
            key, value = match.group(1), match.group(2)
            if SENSITIVE_KEY.search(key) and looks_like_high_entropy_secret(value):
                rule = "HIGH_ENTROPY_SECRET_ASSIGNMENT"
                if (line_number, rule) not in seen:
                    findings.append(Finding(path, line_number, rule, f"high-entropy literal assigned to sensitive key {key}"))
                    seen.add((line_number, rule))

        # Compose/env interpolation contains ':' inside ${VAR:-default}, so it
        # must not be parsed as a literal URI credential.
        if "${" in line:
            continue
        for match in CREDENTIAL_URI.finditer(line):
            password = match.group(1)
            if looks_like_high_entropy_secret(password):
                rule = "CREDENTIAL_IN_URI"
                if (line_number, rule) not in seen:
                    findings.append(Finding(path, line_number, rule, "high-entropy credential embedded in URI"))
                    seen.add((line_number, rule))
    return findings


def git_paths(root: Path, include_untracked: bool) -> list[Path]:
    commands = [["git", "ls-files", "-z"]]
    if include_untracked:
        commands.append(["git", "ls-files", "--others", "--exclude-standard", "-z"])
    relative_paths: set[str] = set()
    for command in commands:
        result = subprocess.run(command, cwd=root, check=True, stdout=subprocess.PIPE)
        relative_paths.update(item for item in result.stdout.decode("utf-8", errors="surrogateescape").split("\0") if item)
    return [root / relative for relative in sorted(relative_paths)]


def should_scan(path: Path) -> bool:
    if path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.name.startswith(".env")


def scan_paths(root: Path, paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    root_resolved = root.resolve()
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        try:
            relative = resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            findings.append(Finding(str(path), 0, "PATH_ESCAPE", "tracked path resolves outside repository"))
            continue
        if resolved.name == ".env" or (resolved.name.startswith(".env.") and not resolved.name.endswith(".example")):
            findings.append(Finding(relative, 0, "TRACKED_ENV_FILE", "runtime env files must not be committed"))
        if not resolved.is_file() or not should_scan(resolved) or resolved.stat().st_size > MAX_TEXT_BYTES:
            continue
        raw = resolved.read_bytes()
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        findings.extend(scan_text(relative, text))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail when committed files contain credential-like literals.")
    parser.add_argument("--include-untracked", action="store_true", help="also scan non-ignored untracked files")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    findings = scan_paths(root, git_paths(root, args.include_untracked))
    if findings:
        print(f"SECRET_HYGIENE_FAILED count={len(findings)}")
        for finding in sorted(findings, key=lambda item: (item.path, item.line, item.rule)):
            print(f"{finding.path}:{finding.line}|{finding.rule}|{finding.message}")
        print("No detected secret values were printed.")
        return 1
    print("SECRET_HYGIENE_OK findings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
