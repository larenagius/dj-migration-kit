"""Read-only spike #2: hot-cue encoding, color palette, beatgrid shape, and
RB<->Traktor filename overlap. Never writes anything."""

import os
from collections import Counter

from pyrekordbox import Rekordbox6Database


def main():
    db = Rekordbox6Database()

    # ---- 1. Hot cue encoding: scan cues, tabulate Kind values ----
    print("=== cue Kind distribution (sample 5000) ===")
    cues = db.get_cue().limit(5000).all()
    kinds = Counter(int(getattr(c, "Kind", -9)) for c in cues)
    print("  Kind counts:", dict(kinds))

    # find a cue that is a hot cue (not memory)
    hot = None
    for c in cues:
        is_mem = getattr(c, "is_memory_cue", None)
        if is_mem is False:
            hot = c
            break
    print("=== example HOT cue ===")
    if hot is not None:
        for a in ["ContentID", "InMsec", "OutMsec", "Kind", "Color",
                  "ColorTableIndex", "Comment", "ActiveLoop", "is_memory_cue",
                  "is_hot_cue"]:
            try:
                print(f"    {a} = {getattr(hot, a)!r}")
            except Exception as e:  # noqa: BLE001
                print(f"    {a} = <err {e}>")
    else:
        print("    none found in sample")

    # color values present
    colors = Counter(int(getattr(c, "Color", -99)) for c in cues)
    print("  Color value counts:", dict(colors))

    # any pyrekordbox color helper?
    print("=== color helpers ===")
    try:
        import pyrekordbox.db6.tables as t
        print("  has tables module; DjmdCue attrs w/ 'color':",
              [a for a in dir(t.DjmdCue) if "olor" in a])
    except Exception as e:  # noqa: BLE001
        print("  tables import err:", e)

    # ---- 2. Beatgrid shape ----
    print("=== beatgrid shape ===")
    contents = db.get_content().limit(50).all()
    for c in contents:
        try:
            files = db.read_anlz_files(c.ID)
        except Exception:
            continue
        for path, anlz in (files or {}).items():
            try:
                bg = anlz.get("beat_grid")
            except Exception:
                bg = None
            if bg is None:
                continue
            print("  beat_grid python type:", type(bg))
            if isinstance(bg, (tuple, list)):
                print("  len:", len(bg))
                for i, part in enumerate(bg):
                    print(f"    part[{i}] type={type(part)} "
                          f"sample={str(part)[:120]}")
            # try structured access
            for attr in ("beats", "bpm", "time", "tempo"):
                try:
                    print(f"    .{attr}: {str(getattr(bg, attr))[:120]}")
                except Exception:
                    pass
            return_after = True
            break
        else:
            continue
        break

    # ---- 3. RB <-> Traktor filename overlap ----
    print("=== RB<->Traktor filename overlap ===")
    rb_names = set()
    for c in db.get_content():
        fp = c.FolderPath or ""
        if fp:
            rb_names.add(os.path.basename(fp).casefold())
    print("  RB unique basenames:", len(rb_names))

    from lxml import etree
    from djmigrationkit.locate import find_traktor_collection
    import sys
    tk = find_traktor_collection(sys.argv[1] if len(sys.argv) > 1 else None)
    if tk is None:
        print("  (no Traktor collection found; skipping overlap check. "
              "Pass a path or set DJMK_TRAKTOR.)")
        return
    parser = etree.XMLParser(huge_tree=True)
    tree = etree.parse(str(tk), parser)
    tk_names = set()
    for loc in tree.iter("LOCATION"):
        f = loc.get("FILE")
        if f:
            tk_names.add(f.casefold())
    print("  Traktor unique basenames:", len(tk_names))
    inter = rb_names & tk_names
    print("  intersection (exact filename match):", len(inter))
    sample = list(inter)[:5]
    print("  sample matches:", sample)


if __name__ == "__main__":
    main()
