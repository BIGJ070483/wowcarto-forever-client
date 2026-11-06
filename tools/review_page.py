"""Generate a private, offline observation form. Never promote inferred positions."""
import json
from pathlib import Path

from PIL import Image


def write_review_page(output, records, review, build, version):
    output = Path(output)
    tile_dir = output / "review-tiles"
    tile_dir.mkdir()
    tiles = {}
    for name, pair in records.items():
        standard = pair.get("standard", {})
        if not standard.get("sha256") or standard.get("error"):
            continue
        # A decoded PNG copy preserves native RGB pixels; no source is written.
        with Image.open(standard["path"]) as image:
            image.convert("RGB").save(tile_dir / f"{name}.png")
        tiles[name] = {
            "width": standard["width"], "height": standard["height"],
            "sha256": {res: pair.get(res, {}).get("sha256") if not pair.get(res, {}).get("error") else None
                       for res in ("standard", "high")},
        }
    components = []
    for component in review["components"]:
        entries = [entry for entry in component["tiles"] if entry["file"] in tiles]
        sizes = {(tiles[entry["file"]]["width"], tiles[entry["file"]]["height"]) for entry in entries}
        if len(sizes) == 1:
            components.append(entries)
        else:
            components.extend([[{**entry, "x": 0, "y": 0}] for entry in entries])
    components.sort(key=len, reverse=True)
    data = {
        "tool_version": version, "build": {key: (build or {}).get(key) for key in ("product", "version")},
        "tiles": tiles, "components": components,
        "links": [{key: link.get(key) for key in ("source", "target", "direction", "mae", "reason")}
                  for link in review["accepted_links"] + review["rejected_links"]],
    }
    resources = Path(__file__).with_name("review")
    payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = (resources / "validation.html").read_text(encoding="utf-8")
    html = html.replace("/*__REVIEW_DATA__*/", payload)
    html = html.replace("/*__REVIEW_SCRIPT__*/", (resources / "validation.js").read_text(encoding="utf-8"))
    (output / "validation.html").write_text(html, encoding="utf-8")
    return {"file": "validation.html", "tiles": len(tiles), "status": "offline_user_review_only",
            "note": "Aucune position ou observation pre-validee. Aucun envoi reseau. Telecharger le JSON pour conserver les saisies."}
