"""Rewrite track paths between machines (e.g. Linux NAS mount -> Mac volume).

A DJ library stores absolute file paths, which differ per machine even when the
music lives on the same NAS share:

    Linux    /mnt/nas/Music/DJ Box/track.mp3
    macOS    /Volumes/personal_folder/Music/DJ Box/track.mp3
    Windows  Y:/Music/DJ Box/track.mp3

:class:`PathMapper` applies ``FROM=TO`` prefix rules. Backslashes are normalized
to ``/``, prefixes match case-insensitively (Windows drive letters vary in case)
and only on a folder boundary, and the longest matching prefix wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class PathRuleError(ValueError):
    pass


def _norm(p: str) -> str:
    return p.replace("\\", "/")


@dataclass
class PathMapper:
    rules: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def parse(cls, specs: list[str] | None) -> "PathMapper":
        """Build from ``["FROM=TO", ...]`` strings (CLI ``--map-path``)."""
        rules = []
        for spec in specs or []:
            if "=" not in spec:
                raise PathRuleError(f"--map-path needs FROM=TO, got: {spec!r}")
            src, dst = spec.split("=", 1)
            src, dst = _norm(src.strip()).rstrip("/"), _norm(dst.strip()).rstrip("/")
            if not src or not dst:
                raise PathRuleError(f"--map-path FROM and TO must both be set: {spec!r}")
            rules.append((src, dst))
        # longest prefix first, so a specific rule beats a general one
        rules.sort(key=lambda r: len(r[0]), reverse=True)
        return cls(rules)

    def map(self, path: str) -> tuple[str, bool]:
        """Return ``(new_path, matched)``. Unmatched paths are returned normalized."""
        p = _norm(path)
        low = p.casefold()
        for src, dst in self.rules:
            s = src.casefold()
            if low == s or low.startswith(s + "/"):
                return dst + p[len(src):], True
        return p, False
