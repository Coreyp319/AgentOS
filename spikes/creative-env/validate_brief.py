#!/usr/bin/env python3
"""validate_brief.py — the model->code contract gate (Design-0023 stage [0]).

PURE PYTHON. No `bpy` import. This is the *only* thing that decides whether a
model-proposed brief is allowed to touch Blender at all. It is deliberately
unit-testable in isolation so the contract can be tested without a GPU.

What it enforces (the "code disposes" boundary):
  * every enum field is an allowlist drawn from brief_schema.json's x-allowlist*
    markers; an off-vocabulary value is REJECTED (never coerced into geometry);
  * structural shape (required keys, types, ranges, hex palette);
  * cross-references resolve (camera.subject names a real element id; motion.field
    names a real element id);
  * the palette is the single source of colour coherence — `clamp_color` maps any
    requested RGB to its NEAREST locked palette entry (Design-0023 §[0]).

Usage:
    python3 validate_brief.py briefs/amber_field.json        # validate + print OK
    python3 validate_brief.py briefs/amber_field.json --json # emit the normalized brief

Exit codes: 0 = valid, 2 = invalid (errors printed to stderr).
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "brief_schema.json")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# The brief-contract version this validator understands (P3.13). A brief whose MAJOR
# differs is REJECTED, not coerced — the two-disposer gate before Unreal becomes a
# second consumer of the contract. Bump MINOR for additive fields, MAJOR for a break.
SUPPORTED_SCHEMA_VERSION = "0.1.0"
SUPPORTED_SCHEMA_MAJOR = int(_SEMVER_RE.match(SUPPORTED_SCHEMA_VERSION).group(1))


class BriefError(ValueError):
    """A brief rejected by the contract. Message is human-readable + actionable."""


# ---------------------------------------------------------------------------
# colour helpers (pure math — the palette clamp is the coherence guarantee)
# ---------------------------------------------------------------------------
def hex_to_rgb(h: str) -> tuple[float, float, float]:
    """'#rrggbb' -> linear-ish 0..1 sRGB tuple (no gamma; clamp distance only)."""
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0.0, min(1.0, c)) for c in rgb)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def clamp_color(rgb: tuple[float, float, float], palette: list[str]) -> tuple[float, float, float]:
    """Return the palette entry NEAREST to `rgb` in sRGB euclidean distance.

    This is the load-bearing coherence primitive: every material albedo/emission
    requested anywhere downstream is run through here, so a colour can never leave
    the locked set even if a later gen step returns off-theme values (Design-0023).
    Deterministic: ties break toward the earlier palette index.
    """
    target = rgb
    best = None
    best_d = None
    for entry in palette:
        pr = hex_to_rgb(entry)
        d = sum((a - b) ** 2 for a, b in zip(pr, target))
        if best_d is None or d < best_d:
            best_d = d
            best = pr
    return best  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# schema-driven allowlist extraction
# ---------------------------------------------------------------------------
def _load_schema() -> dict[str, Any]:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def _allow(node: dict[str, Any], key: str = "x-allowlist") -> list[str] | None:
    v = node.get(key)
    return list(v) if v else None


# ---------------------------------------------------------------------------
# the validator
# ---------------------------------------------------------------------------
def validate(brief: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate `brief` against the contract; return a normalized copy or raise.

    Normalization performed:
      * palette stored lowercase;
      * a `_resolved` block added: subject element + motion target + the path
        endpoints as named anchors — everything the bpy stages need pre-checked,
        so no Blender code re-validates the contract.
    """
    if schema is None:
        schema = _load_schema()
    props = schema["properties"]
    errs: list[str] = []

    if not isinstance(brief, dict):
        raise BriefError("brief must be a JSON object")

    # unknown top-level keys
    for k in brief:
        if k not in props:
            errs.append(f"unknown top-level key: {k!r} (not in schema)")

    # required
    for k in schema.get("required", []):
        if k not in brief:
            errs.append(f"missing required key: {k!r}")

    # schema version (P3.13) — the two-disposer contract gate. A brief MUST declare a
    # SemVer `schema`; an incompatible MAJOR is rejected (never mis-disposed). MINOR/PATCH
    # ahead of us is accepted (additive-only forward compat within a MAJOR).
    sv = brief.get("schema")
    if sv is None:
        errs.append("schema: required — declare the brief-contract version "
                    f"(this disposer understands {SUPPORTED_SCHEMA_VERSION})")
    elif not isinstance(sv, str) or not _SEMVER_RE.match(sv):
        errs.append(f"schema: {sv!r} must be a SemVer string 'MAJOR.MINOR.PATCH'")
    else:
        major = int(_SEMVER_RE.match(sv).group(1))
        if major != SUPPORTED_SCHEMA_MAJOR:
            errs.append(
                f"schema: brief is v{sv} but this disposer only handles MAJOR "
                f"{SUPPORTED_SCHEMA_MAJOR}.x (supported: {SUPPORTED_SCHEMA_VERSION}); "
                f"refusing to dispose an incompatible contract")

    def enum_check(value: str, allowed: list[str], where: str) -> None:
        if value not in allowed:
            errs.append(f"{where}: {value!r} not in allowlist {allowed}")

    def piped_enum_check(value: str, allowed: list[str], where: str) -> None:
        for tag in (t.strip() for t in value.split("|")):
            if tag and tag not in allowed:
                errs.append(f"{where}: tag {tag!r} not in allowlist {allowed}")

    # theme
    if "theme" in brief and not (isinstance(brief["theme"], str) and brief["theme"].strip()):
        errs.append("theme: must be a non-empty string")

    # mood (piped tags)
    if "mood" in brief:
        allow = props["mood"]["x-allowlist-tags"]
        if not isinstance(brief["mood"], str):
            errs.append("mood: must be a string")
        else:
            piped_enum_check(brief["mood"], allow, "mood")

    # palette
    palette = brief.get("palette")
    if not isinstance(palette, list) or not (2 <= len(palette) <= 8):
        errs.append("palette: must be a list of 2..8 hex colours")
        palette = []
    else:
        for c in palette:
            if not (isinstance(c, str) and _HEX_RE.match(c)):
                errs.append(f"palette: {c!r} is not a #rrggbb hex colour")

    # elements
    element_ids: set[str] = set()
    elements = brief.get("elements")
    if not isinstance(elements, list) or not elements:
        errs.append("elements: must be a non-empty list")
        elements = []
    eprops = props["elements"]["items"]["properties"]
    for i, el in enumerate(elements):
        where = f"elements[{i}]"
        if not isinstance(el, dict):
            errs.append(f"{where}: must be an object")
            continue
        for k in el:
            if k not in eprops:
                errs.append(f"{where}: unknown key {k!r}")
        for req in props["elements"]["items"]["required"]:
            if req not in el:
                errs.append(f"{where}: missing {req!r}")
        if "id" in el:
            if not (isinstance(el["id"], str) and _ID_RE.match(el["id"])):
                errs.append(f"{where}.id: {el.get('id')!r} must match {_ID_RE.pattern}")
            elif el["id"] in element_ids:
                errs.append(f"{where}.id: duplicate id {el['id']!r}")
            else:
                element_ids.add(el["id"])
        if "kind" in el:
            enum_check(el["kind"], eprops["kind"]["x-allowlist"], f"{where}.kind")
        if "scale" in el:
            enum_check(el["scale"], eprops["scale"]["x-allowlist"], f"{where}.scale")
        if "layout" in el:
            enum_check(el["layout"], eprops["layout"]["x-allowlist"], f"{where}.layout")
        if "count" in el and not (isinstance(el["count"], int) and 1 <= el["count"] <= 200000):
            errs.append(f"{where}.count: {el.get('count')!r} out of range 1..200000")

    # lighting
    lighting = brief.get("lighting")
    if not isinstance(lighting, dict):
        errs.append("lighting: must be an object")
    else:
        lp = props["lighting"]["properties"]
        if "key" in lighting:
            enum_check(lighting["key"], lp["key"]["x-allowlist"], "lighting.key")
        else:
            errs.append("lighting.key: required")
        if "intensity" in lighting:
            enum_check(lighting["intensity"], lp["intensity"]["x-allowlist"], "lighting.intensity")
        else:
            errs.append("lighting.intensity: required")

    # motion (optional) — field must reference a real element id
    motion = brief.get("motion")
    if motion is not None:
        if not isinstance(motion, dict):
            errs.append("motion: must be an object")
        else:
            mp = props["motion"]["properties"]
            if "field" in motion:
                # motion.field is BOTH an allowlisted animation kind in schema AND, in the
                # amber brief, the element id it animates. We accept either: an allowlisted
                # kind, OR an element id present in elements[]. (Design-0023 uses "field" as
                # the grass element id; the schema's allowlist describes animation kinds.)
                fld = motion["field"]
                if fld not in mp["field"]["x-allowlist"] and fld not in element_ids:
                    errs.append(
                        f"motion.field: {fld!r} is neither an allowlisted animation "
                        f"kind {mp['field']['x-allowlist']} nor a known element id "
                        f"{sorted(element_ids)}"
                    )
            if "speed" in motion:
                enum_check(motion["speed"], mp["speed"]["x-allowlist"], "motion.speed")

    # camera
    camera = brief.get("camera")
    if not isinstance(camera, dict):
        errs.append("camera: must be an object")
        camera = {}
    cp = props["camera"]["properties"]
    for req in props["camera"]["required"]:
        if req not in camera:
            errs.append(f"camera.{req}: required")
    if "move" in camera:
        enum_check(camera["move"], cp["move"]["x-allowlist"], "camera.move")
    if "arc" in camera:
        enum_check(camera["arc"], cp["arc"]["x-allowlist"], "camera.arc")
    if "easing" in camera:
        enum_check(camera["easing"], cp["easing"]["x-allowlist"], "camera.easing")
    if "duration_s" in camera and not (isinstance(camera["duration_s"], (int, float)) and 1 <= camera["duration_s"] <= 60):
        errs.append("camera.duration_s: must be a number in 1..60")
    if "subject" in camera and camera["subject"] not in element_ids:
        errs.append(f"camera.subject: {camera['subject']!r} is not an element id {sorted(element_ids)}")

    # path
    path = brief.get("path")
    if not isinstance(path, dict):
        errs.append("path: must be an object")
    else:
        pp = props["path"]["properties"]
        for req in props["path"]["required"]:
            if req not in path:
                errs.append(f"path.{req}: required")
        if "render_as" in path:
            enum_check(path["render_as"], pp["render_as"]["x-allowlist"], "path.render_as")
        if "from" in path:
            enum_check(path["from"], pp["from"]["x-allowlist"], "path.from")
        if "to" in path:
            enum_check(path["to"], pp["to"]["x-allowlist"], "path.to")

    # render (optional)
    render = brief.get("render")
    if render is not None:
        if not isinstance(render, dict):
            errs.append("render: must be an object")
        elif "style" in render:
            enum_check(render["style"], props["render"]["properties"]["style"]["x-allowlist"], "render.style")

    # bindings (optional) — allowlisted keys AND values
    bindings = brief.get("bindings")
    if bindings is not None:
        if not isinstance(bindings, dict):
            errs.append("bindings: must be an object")
        else:
            allow_keys = props["bindings"]["x-allowlist-keys"]
            allow_vals = props["bindings"]["x-allowlist-values"]
            for k, v in bindings.items():
                if k not in allow_keys:
                    errs.append(f"bindings: key {k!r} not in allowlist {allow_keys}")
                if not isinstance(v, str) or v not in allow_vals:
                    errs.append(f"bindings[{k!r}]: value {v!r} not in allowlist {allow_vals}")

    if errs:
        raise BriefError("brief rejected (%d error%s):\n  - %s" % (
            len(errs), "" if len(errs) == 1 else "s", "\n  - ".join(errs)))

    # ---- normalize ----
    norm = json.loads(json.dumps(brief))  # deep copy
    norm["palette"] = [c.lower() for c in norm["palette"]]
    norm["_resolved"] = {
        "schema_version": norm.get("schema"),
        "element_ids": sorted(element_ids),
        "subject": camera.get("subject"),
        "palette_rgb": [hex_to_rgb(c) for c in norm["palette"]],
    }
    return norm


def load_and_validate(path: str) -> dict[str, Any]:
    with open(path) as f:
        brief = json.load(f)
    return validate(brief)


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    path = argv[0]
    want_json = "--json" in argv[1:]
    try:
        norm = load_and_validate(path)
    except (BriefError, json.JSONDecodeError, OSError) as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 2
    if want_json:
        print(json.dumps(norm, indent=2))
    else:
        print(f"OK: {path} is a valid brief")
        print(f"  theme:   {norm['theme']}")
        print(f"  palette: {norm['palette']}")
        print(f"  subject: {norm['_resolved']['subject']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
