"""Optional catalogue adapter; the conversion engine has no HARVEN dependency."""
from pathlib import Path
import json
from .core import ensure_group


def import_catalog(project, path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    added, skipped = 0, 0
    existing = {a["id"] for a in project["assets"]}
    # Generic catalog: entries [{id, source, normal?, group, region?, pivot?}].
    # HARVEN: pixel_group + thumbnail.path/region; procedural entries are skipped.
    root = next((p for p in path.parents if (p / "game/project.godot").exists()), path.parent)
    def resolve(value):
        if not value:
            return None
        text = str(value)
        if text.startswith("res://"):
            return str((root / "game" / text[6:]).resolve())
        source = Path(text)
        candidates = [source] if source.is_absolute() else [path.parent/source, root/"game/assets/art/assets"/source, root/"HARVEN-ART/assets"/source]
        return str(next((p for p in candidates if p.exists()), candidates[0]).resolve())
    for entry in data.get("entries", data.get("assets", [])):
        thumb = entry.get("thumbnail") or {}
        source = resolve(entry.get("source", thumb.get("path")))
        if not source or Path(source).suffix.lower() != ".png" or not Path(source).exists() or entry["id"] in existing:
            skipped += 1
            continue
        group = entry.get("pixel_group", entry.get("group", entry["id"]))
        asset = {"id": entry["id"], "source": source, "group": group,
                 "normal": resolve(entry.get("normal")), "metadata": entry.get("metadata", {})}
        for key in ("region", "pivot", "settings"):
            value = entry.get(key, thumb.get(key))
            if value is not None:
                asset[key] = value
        ensure_group(project, group)
        project["assets"].append(asset)
        existing.add(entry["id"])
        added += 1
    return f"카탈로그 {added}개 가져옴 · PNG 없음/중복 {skipped}개 건너뜀"
