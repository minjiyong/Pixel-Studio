"""CPU reference for pixel.asset.project v1. No GUI or engine dependencies.

Sampling is nearest at destination pixel centers. Color matching uses squared
sRGB distance with Rec.709 weights (not a linear-light averaging operation).
Normals are data, decoded to vectors, optionally discretized, then normalized.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
import hashlib
import json
import math
import re
import tempfile
from typing import Callable

import numpy as np
from PIL import Image

FORMAT = "pixel.asset.project"
VERSION = 1
ALGORITHM = "pixel-center-rgb709-v1"
BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32)
WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


@dataclass
class Settings:
    mode: str = "target"
    width: int = 100
    height: int = 100
    pixel_step: int = 4
    colors: int = 32
    fit: str = "contain"
    dither: float = 0.0
    alpha_threshold: int = 128
    alpha_mode: str = "binary"
    outline: bool = False
    normal_levels: int = 0
    normal_flip_y: bool = False
    normal_flip_x: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        values = {k: v for k, v in data.items() if k in {f.name for f in fields(cls)}}
        for key in ("width", "height", "pixel_step", "colors", "alpha_threshold", "normal_levels"):
            value = values.get(key)
            if isinstance(value, float) and math.isfinite(value) and value.is_integer():
                values[key] = int(value)
        result = cls(**values)
        result.validate()
        return result

    def validate(self):
        if self.mode not in ("target", "step") or self.fit not in ("contain", "stretch"):
            raise ValueError("잘못된 출력 크기 모드입니다.")
        if self.alpha_mode not in ("binary", "preserve"):
            raise ValueError("잘못된 투명도 모드입니다.")
        for key, low, high in (("width", 1, 4096), ("height", 1, 4096), ("pixel_step", 1, 128),
                               ("colors", 2, 255), ("alpha_threshold", 1, 255), ("normal_levels", 0, 32)):
            value = getattr(self, key)
            if not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key}: {low}~{high} 정수가 필요합니다.")
        if not 0 <= self.dither <= 1:
            raise ValueError("디더링은 0~1 사이여야 합니다.")


def new_project() -> dict:
    return {"format": FORMAT, "version": VERSION, "algorithm": ALGORITHM,
            "settings": asdict(Settings()), "groups": {}, "assets": []}


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        temporary = Path(f.name)
    temporary.replace(path)


def load_project(path: Path) -> dict:
    project = json.loads(path.read_text(encoding="utf-8-sig"))
    if project.get("format") != FORMAT or project.get("version") != VERSION:
        raise ValueError("Pixel Studio 프로젝트 v1 파일을 선택하세요.")
    if project.get("algorithm", ALGORITHM) != ALGORITHM:
        raise ValueError("지원하지 않는 변환 알고리즘 버전입니다.")
    project["settings"] = asdict(Settings.from_dict(project["settings"]))
    ids = set()
    for asset in project["assets"]:
        if not asset.get("id") or asset["id"] in ids:
            raise ValueError("에셋 ID는 비어 있지 않고 고유해야 합니다.")
        ids.add(asset["id"])
        for key in ("source", "normal"):
            if asset.get(key):
                source = Path(asset[key])
                asset[key] = str((path.parent / source).resolve()) if not source.is_absolute() else str(source)
        ensure_group(project, asset["group"])
    for group in project["groups"].values():
        group["palette"] = checked_palette(group.get("palette", []))
        locked = group.get("locked", [])
        if any(not isinstance(i, (int, float)) or i != int(i) or not 0 <= i < len(group["palette"]) for i in locked):
            raise ValueError("잠긴 팔레트 색 번호가 잘못되었습니다.")
        group["locked"] = [int(i) for i in locked]
        Settings.from_dict({**project["settings"], **group.get("settings", {})})
    for asset in project["assets"]:
        settings_for(project, asset)
    return project


def save_project(path: Path, project: dict):
    # Absolute paths are intentional: portable relative paths may be supplied by
    # a catalogue, but moving a project must never silently rebind source files.
    atomic_json(path, project)


def ensure_group(project: dict, name: str) -> dict:
    return project["groups"].setdefault(name, {"palette": [], "locked": [], "settings": {}})


def settings_for(project: dict, asset: dict) -> Settings:
    data = dict(project["settings"])
    data.update(project["groups"].get(asset["group"], {}).get("settings", {}))
    data.update(asset.get("settings", {}))
    return Settings.from_dict(data)


def checked_palette(palette: list) -> list[list[int]]:
    if len(palette) > 255:
        raise ValueError("팔레트는 최대 255색입니다. 투명색은 별도로 저장됩니다.")
    result = []
    for color in palette:
        if len(color) != 3 or any(not isinstance(c, (int, float)) or not math.isfinite(c) or c != int(c) or not 0 <= c <= 255 for c in color):
            raise ValueError("팔레트 색상은 RGB 0~255 정수여야 합니다.")
        result.append([int(c) for c in color])
    return result


def load_rgba(path: str, region=None) -> np.ndarray:
    source = Path(path)
    if source.suffix.lower() != ".png":
        raise ValueError("이미지 입출력은 PNG만 지원합니다.")
    with Image.open(source) as img:
        if img.format != "PNG":
            raise ValueError("파일 내용이 PNG가 아닙니다.")
        if region is not None:
            x, y, w, h = map(int, region)
            if min(x, y) < 0 or min(w, h) < 1 or x + w > img.width or y + h > img.height:
                raise ValueError("프레임 영역이 원본 크기를 벗어납니다.")
            img = img.crop((x, y, x + w, y + h))
        return np.array(img.convert("RGBA"))


def layout(size: tuple[int, int], settings: Settings) -> dict:
    w, h = size
    if settings.mode == "step":
        ow, oh = math.ceil(w / settings.pixel_step), math.ceil(h / settings.pixel_step)
        rw, rh = ow, oh
    else:
        ow, oh = settings.width, settings.height
        if settings.fit == "contain":
            ratio = min(ow / w, oh / h)
            rw, rh = max(1, math.floor(w * ratio + .5)), max(1, math.floor(h * ratio + .5))
        else:
            rw, rh = ow, oh
    return {"size": [ow, oh], "content": [(ow-rw)//2, (oh-rh)//2, rw, rh], "source_size": [w, h]}


def sample(source: np.ndarray, spec: dict) -> np.ndarray:
    ow, oh = spec["size"]
    x, y, rw, rh = spec["content"]
    h, w = source.shape[:2]
    xs = np.minimum(((np.arange(rw) + .5) * w / rw).astype(int), w - 1)
    ys = np.minimum(((np.arange(rh) + .5) * h / rh).astype(int), h - 1)
    result = np.zeros((oh, ow, 4), dtype=np.uint8)
    result[y:y+rh, x:x+rw] = source[ys[:, None], xs[None, :]]
    return result


def palette_from_arrays(arrays, count: int, locked=()) -> list[list[int]]:
    """Bounded histogram + deterministic weighted median cuts. No random seeds."""
    fixed = checked_palette(list(locked))
    fixed = [list(c) for c in dict.fromkeys(tuple(c) for c in fixed)]
    if len(fixed) > count:
        raise ValueError("잠근 색 수보다 팔레트 색 수가 작습니다.")
    histogram = np.zeros(32768, dtype=np.float64)
    sums = np.zeros((32768, 3), dtype=np.float64)
    for rgba in arrays:
        h, w = rgba.shape[:2]
        # Stratify in TWO dimensions. A linear stride aliases power-of-two
        # atlases into a handful of columns and can entirely miss small sprites.
        if w*h > 4096:
            nx = min(w, max(1, int(math.sqrt(4096*w/h))))
            ny = min(h, max(1, 4096//nx))
            xs = ((np.arange(nx)+.5)*w/nx).astype(int)
            ys = ((np.arange(ny)+.5)*h/ny).astype(int)
            flat = rgba[ys[:, None], xs[None, :]].reshape(-1, 4)
        else:
            flat = rgba.reshape(-1, 4)
        flat = flat[flat[:, 3] > 0]
        if not len(flat):
            continue
        rgb = flat[:, :3].astype(np.int32)
        idx = ((rgb[:, 0] >> 3) << 10) | ((rgb[:, 1] >> 3) << 5) | (rgb[:, 2] >> 3)
        weight = flat[:, 3].astype(float) / 255 / len(flat)
        histogram += np.bincount(idx, weight, minlength=32768)
        for channel in range(3):
            sums[:, channel] += np.bincount(idx, weight * rgb[:, channel], minlength=32768)
    occupied = np.flatnonzero(histogram)
    if not len(occupied):
        return fixed or [[0, 0, 0]]
    rgb = sums[occupied] / histogram[occupied, None]
    weights = histogram[occupied]
    boxes = [np.arange(len(occupied))]
    def score(box):
        return float(np.max(np.ptp(rgb[box], axis=0) * WEIGHTS) * weights[box].sum()) if len(box) > 1 else -1
    while len(boxes) < count - len(fixed):
        split = max(range(len(boxes)), key=lambda i: score(boxes[i]))
        box = boxes[split]
        if len(box) < 2:
            break
        channel = int(np.argmax(np.ptp(rgb[box], axis=0) * WEIGHTS))
        box = box[np.argsort(rgb[box, channel], kind="stable")]
        cut = int(np.searchsorted(np.cumsum(weights[box]), weights[box].sum() / 2)) + 1
        cut = max(1, min(cut, len(box) - 1))
        boxes[split:split+1] = [box[:cut], box[cut:]]
    generated = [np.floor(np.average(rgb[b], axis=0, weights=weights[b]) + .5).astype(int).tolist()
                 for b in boxes] if count > len(fixed) else []
    generated.sort(key=lambda c: (float(np.dot(c, WEIGHTS)), c))
    return [list(c) for c in dict.fromkeys(tuple(c) for c in fixed + generated)]


def generate_group(project: dict, name: str, regenerate=False) -> list:
    group = ensure_group(project, name)
    if group.get("palette") and not regenerate:
        return checked_palette(group["palette"])
    members = [a for a in project["assets"] if a["group"] == name]
    count = Settings.from_dict({**project["settings"], **group.get("settings", {})}).colors
    old = group.get("palette", [])
    locked = [old[i] for i in group.get("locked", []) if 0 <= i < len(old)]
    palette = palette_from_arrays((load_rgba(a["source"], a.get("region")) for a in members), count, locked)
    group["palette"] = palette
    group["locked"] = list(range(len({tuple(c) for c in locked})))
    return palette


def nearest_indices(rgb: np.ndarray, palette: list, dither: float = 0) -> np.ndarray:
    h, w = rgb.shape[:2]
    colors = rgb.astype(np.float32)
    if dither:
        yy, xx = np.indices((h, w))
        bias = ((BAYER[yy % 4, xx % 4] + .5) / 16 - .5) * 64 * dither
        colors = np.clip(colors + bias[..., None], 0, 255)
    colors = colors.reshape(-1, 3)
    pal = np.asarray(palette, dtype=np.float32)
    result = np.empty(len(colors), dtype=np.uint8)
    # Bounded temporary allocations even for large exports and long batches.
    for start in range(0, len(colors), 4096):
        delta = colors[start:start+4096, None, :] - pal[None, :, :]
        distance = np.sum(delta * delta * WEIGHTS, axis=2)
        result[start:start+4096] = np.argmin(distance, axis=1)
    return result.reshape(h, w)


def transform(source: np.ndarray, normal: np.ndarray | None, settings: Settings, palette: list) -> dict:
    settings.validate()
    palette = checked_palette(palette)
    if not palette:
        raise ValueError("팔레트를 먼저 생성하세요.")
    if normal is not None and normal.shape != source.shape:
        raise ValueError("컬러와 노멀의 원본 크기/프레임 영역이 같아야 합니다.")
    spec = layout((source.shape[1], source.shape[0]), settings)
    rgba = sample(source, spec)
    if settings.alpha_mode == "binary":
        rgba[..., 3] = np.where(rgba[..., 3] >= settings.alpha_threshold, 255, 0)
    indices = nearest_indices(rgba[..., :3], palette, settings.dither)
    if settings.outline:
        opaque = rgba[..., 3] > 0
        padded = np.pad(opaque, 1)
        interior = padded[1:-1, :-2] & padded[1:-1, 2:] & padded[:-2, 1:-1] & padded[2:, 1:-1]
        indices[opaque & ~interior] = int(np.argmin(np.array(palette) @ WEIGHTS))
    rgba[..., :3] = np.asarray(palette, dtype=np.uint8)[indices]
    rgba[rgba[..., 3] == 0, :3] = 0
    result = {"color": rgba, "indices": indices, "layout": spec, "normal": None}
    if normal is not None:
        n = sample(normal, spec)
        vectors = n[..., :3].astype(np.float32) / 255 * 2 - 1
        if settings.normal_flip_x:
            vectors[..., 0] *= -1
        if settings.normal_flip_y:
            vectors[..., 1] *= -1
        if settings.normal_levels:
            vectors = np.floor(vectors * settings.normal_levels + .5) / settings.normal_levels
        length = np.linalg.norm(vectors, axis=2, keepdims=True)
        vectors = np.divide(vectors, length, out=np.zeros_like(vectors), where=length > 1e-6)
        vectors[length[..., 0] <= 1e-6] = [0, 0, 1]
        n[..., :3] = np.clip(np.floor((vectors * .5 + .5) * 255 + .5), 0, 255).astype(np.uint8)
        n[..., 3] = rgba[..., 3]
        n[n[..., 3] == 0, :3] = [128, 128, 255]
        result["normal"] = n
    return result


def convert_asset(project: dict, asset: dict) -> dict:
    palette = generate_group(project, asset["group"])
    source = load_rgba(asset["source"], asset.get("region"))
    normal = load_rgba(asset["normal"], asset.get("region")) if asset.get("normal") else None
    result = transform(source, normal, settings_for(project, asset), palette)
    result["palette"] = palette
    result["source"] = source
    return result


def lit_preview(color: np.ndarray, normal: np.ndarray | None, angle: float, elevation=45) -> np.ndarray:
    if normal is None:
        return color.copy()
    a, e = math.radians(angle), math.radians(elevation)
    light = np.array([math.cos(a)*math.cos(e), math.sin(a)*math.cos(e), math.sin(e)])
    n = normal[..., :3].astype(float) / 255 * 2 - 1
    n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-6)
    shade = .25 + .75 * np.maximum(0, n @ light)
    result = color.copy()
    result[..., :3] = np.clip(color[..., :3] * shade[..., None], 0, 255).astype(np.uint8)
    return result


def safe_name(value: str) -> str:
    name = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip(". ")[:90] or "asset"
    return name + "_" + hashlib.sha256(value.encode()).hexdigest()[:8]


def fingerprint(project: dict, asset: dict) -> str:
    digest = hashlib.sha256()
    group = project["groups"][asset["group"]]
    digest.update(json.dumps([ALGORITHM, asset, asdict(settings_for(project, asset)), group.get("palette")], sort_keys=True).encode())
    for key in ("source", "normal"):
        if asset.get(key):
            with open(asset[key], "rb") as f:
                for block in iter(lambda: f.read(1024*1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def save_png(path: Path, rgba: np.ndarray, indices=None, palette=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if palette is not None and np.all((rgba[..., 3] == 0) | (rgba[..., 3] == 255)):
        data = indices.copy()
        data[rgba[..., 3] == 0] = len(palette)
        img = Image.fromarray(data).convert("P")
        colors = [c for rgb in palette for c in rgb] + [0, 0, 0]
        img.putpalette(colors + [0] * (768-len(colors)))
        img.info["transparency"] = len(palette)
    else:
        img = Image.fromarray(rgba)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as f:
        temporary = Path(f.name)
    try:
        img.save(temporary, format="PNG", optimize=True)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def export_batch(project: dict, destination: Path, asset_ids=None, progress: Callable | None = None,
                 cancelled: Callable | None = None, incremental=True) -> dict:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "pixel-manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    previous = {a["id"]: a for a in old.get("assets", [])}
    manifest = {"format": "pixel.asset.export", "version": 1, "algorithm": ALGORITHM, "assets": list(previous.values())}
    records = {a["id"]: a for a in manifest["assets"]}
    selected = [a for a in project["assets"] if asset_ids is None or a["id"] in asset_ids]
    report = {"written": 0, "skipped": 0, "failed": [], "cancelled": False}
    for number, asset in enumerate(selected):
        if cancelled and cancelled():
            report["cancelled"] = True
            break
        try:
            generate_group(project, asset["group"])
            signature = fingerprint(project, asset)
            prior = previous.get(asset["id"], {})
            prefix = safe_name(asset["group"]) + "/" + safe_name(asset["id"])
            color_file, normal_file = prefix + ".png", prefix + "_normal.png"
            if incremental and prior.get("fingerprint") == signature and (destination / color_file).exists() and (
                    not asset.get("normal") or (destination / normal_file).exists()):
                report["skipped"] += 1
            else:
                result = convert_asset(project, asset)
                outputs = [destination / color_file] + ([destination / normal_file] if result["normal"] is not None else [])
                sources = {Path(a[k]).resolve() for a in project["assets"] for k in ("source", "normal") if a.get(k)}
                if any(p.resolve() in sources for p in outputs):
                    raise ValueError("출력 경로가 원본과 겹칩니다.")
                save_png(outputs[0], result["color"], result["indices"], result["palette"])
                if result["normal"] is not None:
                    save_png(outputs[1], result["normal"])
                spec = result["layout"]
                source_pivot = asset.get("pivot", [spec["source_size"][0]/2, spec["source_size"][1]])
                x, y, w, h = spec["content"]
                pivot = [x + source_pivot[0]*w/spec["source_size"][0], y + source_pivot[1]*h/spec["source_size"][1]]
                records[asset["id"]] = {"id": asset["id"], "group": asset["group"], "color": color_file,
                    "normal": normal_file if result["normal"] is not None else None, "fingerprint": signature,
                    "layout": spec, "pivot": pivot, "source_pivot": source_pivot,
                    "metadata": asset.get("metadata", {}), "settings": asdict(settings_for(project, asset)),
                    "palette": result["palette"], "png_bytes": sum(p.stat().st_size for p in outputs),
                    "rgba8_bytes_no_mipmaps": spec["size"][0]*spec["size"][1]*4*len(outputs)}
                report["written"] += 1
                manifest["assets"] = list(records.values())
                atomic_json(manifest_path, manifest)
        except Exception as exc:
            report["failed"].append({"id": asset["id"], "error": str(exc)})
        if progress:
            progress(number+1, len(selected), asset["id"])
    atomic_json(destination / "pixel-report.json", report)
    return report


def add_pngs(project: dict, paths: list[str], group: str | None = None) -> int:
    sources = {a["source"] for a in project["assets"]}
    added = 0
    for filename in sorted(set(paths)):
        path = Path(filename).resolve()
        if path.suffix.lower() != ".png" or path.stem.lower().endswith(("_normal", "_norm", "_n")):
            continue
        if str(path) in sources:
            continue
        group_id = group or path.stem
        ensure_group(project, group_id)
        normal = next((p for suffix in ("_normal", "_norm", "_n") if (p := path.with_stem(path.stem+suffix)).exists()), None)
        identity = path.stem + "-" + hashlib.sha256(str(path).encode()).hexdigest()[:8]
        project["assets"].append({"id": identity, "source": str(path), "normal": str(normal) if normal else None,
                                  "group": group_id, "metadata": {}})
        sources.add(str(path))
        added += 1
    return added
