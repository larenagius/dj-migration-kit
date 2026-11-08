"""Write a rekordbox.xml library that Rekordbox (Mac/Windows) can import.

The rekordbox XML format is publicly documented by AlphaTheta ("rekordbox XML
format list"). What we write:

  <DJ_PLAYLISTS Version="1.0.0">
    <PRODUCT Name="DJ Migration Kit" .../>
    <COLLECTION Entries="N">
      <TRACK TrackID Name Artist ... Location="file://localhost/<url-encoded path>">
        <POSITION_MARK Name Type="0|4" Start="<sec>" [End="<sec>"] Num="-1|0..7"
                       Red Green Blue/>
      </TRACK>
    </COLLECTION>
    <PLAYLISTS>
      <NODE Type="0" Name="ROOT" Count="n">            folders: Type=0, Count
        <NODE Type="1" Name="x" KeyType="0" Entries="n"> playlists: Type=1
          <TRACK Key="<TrackID>"/>

POSITION_MARK: Type 0 = cue, 4 = loop; Num -1 = memory cue, 0..7 = hot cue A..H.

Safety: this module only ever creates a *new* file. It refuses to write a file
named ``rekordbox.xml`` (the conventional name of a user's own export) and never
overwrites an existing file -- it picks a timestamped name instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from lxml import etree

from .. import __version__
from ..traktor_io.safe_output import atomic_write_bytes

PROTECTED_NAME = "rekordbox.xml"
DEFAULT_NAME = "mixxx-to-rekordbox.xml"

MARK_CUE = 0
MARK_LOOP = 4
MAX_HOT_CUES = 8  # A..H on CDJs and in rekordbox


class RekordboxXmlOverwriteError(RuntimeError):
    """Raised if a write would replace a protected or existing file."""


@dataclass
class PositionMark:
    start_s: float
    num: int = -1  # -1 memory, 0..7 hot cue slot
    type: int = MARK_CUE
    end_s: Optional[float] = None
    name: str = ""
    rgb: Optional[tuple[int, int, int]] = None


@dataclass
class XmlTrack:
    track_id: int
    path: str  # absolute path *as the Rekordbox machine sees it*
    attrs: dict[str, str] = field(default_factory=dict)
    marks: list[PositionMark] = field(default_factory=list)


@dataclass
class XmlNode:
    """A folder (children) or playlist (track_ids) in the PLAYLISTS tree."""

    name: str
    is_folder: bool = False
    track_ids: list[int] = field(default_factory=list)
    children: list["XmlNode"] = field(default_factory=list)


_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _clean(s) -> str:
    """Strip characters XML 1.0 can't carry (they occasionally appear in tags)."""
    return _ILLEGAL.sub("", str(s))


def path_to_location(path: str) -> str:
    """Absolute path -> rekordbox Location URL (``file://localhost/...``)."""
    p = path.replace("\\", "/")
    encoded = quote(p, safe="/:")
    if p.startswith("/"):
        return "file://localhost" + encoded  # POSIX: /Volumes/... or /home/...
    return "file://localhost/" + encoded  # Windows: Y:/Music/...


def _fmt_s(seconds: float) -> str:
    return f"{max(seconds, 0.0):.3f}"


def build_xml(tracks: list[XmlTrack], playlists: list[XmlNode]) -> bytes:
    root = etree.Element("DJ_PLAYLISTS", Version="1.0.0")
    etree.SubElement(root, "PRODUCT", Name="DJ Migration Kit",
                     Version=__version__, Company="DJ Migration Kit")

    col = etree.SubElement(root, "COLLECTION", Entries=str(len(tracks)))
    for t in tracks:
        el = etree.SubElement(col, "TRACK", TrackID=str(t.track_id))
        for k, v in t.attrs.items():
            if v not in (None, ""):
                el.set(k, _clean(v))
        el.set("Location", path_to_location(t.path))
        for m in t.marks:
            me = etree.SubElement(el, "POSITION_MARK", Name=_clean(m.name),
                                  Type=str(m.type), Start=_fmt_s(m.start_s))
            if m.end_s is not None:
                me.set("End", _fmt_s(m.end_s))
            me.set("Num", str(m.num))
            if m.rgb is not None:
                r, g, b = m.rgb
                me.set("Red", str(r))
                me.set("Green", str(g))
                me.set("Blue", str(b))

    pls = etree.SubElement(root, "PLAYLISTS")
    _write_node(pls, XmlNode(name="ROOT", is_folder=True, children=playlists))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _write_node(parent, node: XmlNode) -> None:
    if node.is_folder:
        el = etree.SubElement(parent, "NODE", Type="0", Name=_clean(node.name),
                              Count=str(len(node.children)))
        for child in node.children:
            _write_node(el, child)
    else:
        el = etree.SubElement(parent, "NODE", Name=_clean(node.name), Type="1",
                              KeyType="0", Entries=str(len(node.track_ids)))
        for tid in node.track_ids:
            etree.SubElement(el, "TRACK", Key=str(tid))


def resolve_output_path(out: str | Path | None, default_dir: Path) -> Path:
    """Pick a safe output file: never ``rekordbox.xml``, never an existing file.

    ``out`` may be a directory (we add the default name), a file path, or None
    (``default_dir``/mixxx-to-rekordbox.xml). If the file exists we add a
    timestamp rather than overwrite it.
    """
    if out is None:
        target = default_dir / DEFAULT_NAME
    else:
        target = Path(out)
        if target.is_dir():
            target = target / DEFAULT_NAME
    if target.name.casefold() == PROTECTED_NAME:
        raise RekordboxXmlOverwriteError(
            f"Refusing to write '{PROTECTED_NAME}' - that is the usual name of your own "
            f"Rekordbox export. Choose another name, e.g. '{DEFAULT_NAME}'."
        )
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = target.with_name(f"{target.stem}-{stamp}{target.suffix}")
        i = 1
        while candidate.exists():
            candidate = target.with_name(f"{target.stem}-{stamp}-{i}{target.suffix}")
            i += 1
        target = candidate
    return target


def write_xml(path: Path, tracks: list[XmlTrack], playlists: list[XmlNode]) -> Path:
    if path.name.casefold() == PROTECTED_NAME or path.exists():
        raise RekordboxXmlOverwriteError(f"Refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write_bytes(path, build_xml(tracks, playlists))
