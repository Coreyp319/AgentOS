#!/usr/bin/env python3
"""check-contrast.py — deterministic WCAG contrast gate for a KDE .colors scheme.

The "code disposes" verifier for the Aurora light/dark colour identity: parse a
KColorScheme .colors file and check the body / selection / link / semantic
foreground↔background pairings against WCAG 2.x AA (4.5:1 normal text, 3:1 large)
and AAA (7:1). The Style Charter scores contrast too (ADR-0034); this is the
standalone authoring gate, so a scheme edit is verified before it is blessed —
no eyeballing a colour for legibility. Pure stdlib.

    check-contrast.py <scheme.colors> [<scheme2.colors> ...]
    check-contrast.py --json <scheme.colors>

Exit 0 iff every checked pairing clears AA; 1 otherwise (so it can gate a commit).
"""
from __future__ import annotations
import configparser, json, sys


def _lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb) -> float:
    r, g, b = (_lin(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg, bg) -> float:
    l1, l2 = _lum(fg), _lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(s):
    try:
        parts = [int(x) for x in str(s).split(",")[:3]]
        return tuple(parts) if len(parts) == 3 else None
    except Exception:
        return None


# (label, fg "Set:Key", bg "Set:Key"). The pairings that carry legibility — the
# ones the charter's status-semantics + body-text principles depend on.
PAIRS = [
    ("window body text", "Window:ForegroundNormal",    "Window:BackgroundNormal"),
    ("view body text",   "View:ForegroundNormal",      "View:BackgroundNormal"),
    ("button label",     "Button:ForegroundNormal",    "Button:BackgroundNormal"),
    ("header label",     "Header:ForegroundNormal",     "Header:BackgroundNormal"),
    ("tooltip text",     "Tooltip:ForegroundNormal",    "Tooltip:BackgroundNormal"),
    ("selected text",    "Selection:ForegroundNormal",  "Selection:BackgroundNormal"),
    ("link (view)",      "View:ForegroundLink",         "View:BackgroundNormal"),
    ("visited (view)",   "View:ForegroundVisited",      "View:BackgroundNormal"),
    ("positive (view)",  "View:ForegroundPositive",     "View:BackgroundNormal"),
    ("neutral (view)",   "View:ForegroundNeutral",      "View:BackgroundNormal"),
    ("negative (view)",  "View:ForegroundNegative",     "View:BackgroundNormal"),
    ("link (window)",    "Window:ForegroundLink",       "Window:BackgroundNormal"),
    ("negative (button)","Button:ForegroundNegative",   "Button:BackgroundNormal"),
    ("selected (alt band)","Selection:ForegroundNormal", "Selection:BackgroundAlternate"),
]

# Non-text UI contrast (WCAG 1.4.11 / 2.4.13): the focus ring must clear 3:1 against the
# surface it sits on. The Aurora ring is SOLID (variables.css --focus-ring-color), so this is
# checkable straight from the scheme's DecorationFocus vs each BackgroundNormal.
NONTEXT = [
    ("focus ring (window)", "Window:DecorationFocus", "Window:BackgroundNormal"),
    ("focus ring (view)",   "View:DecorationFocus",   "View:BackgroundNormal"),
    ("focus ring (button)", "Button:DecorationFocus", "Button:BackgroundNormal"),
    ("focus ring (header)", "Header:DecorationFocus", "Header:BackgroundNormal"),
    # engaged (hover) states — the indicator the style binds on :hovered must also clear 3:1
    ("hover ring (button)", "Button:DecorationHover", "Button:BackgroundNormal"),
    ("hover ring (window)", "Window:DecorationHover", "Window:BackgroundNormal"),
    ("hover ring (view)",   "View:DecorationHover",   "View:BackgroundNormal"),
]

AA, AAA, NONTEXT_MIN = 4.5, 7.0, 3.0   # normal-text thresholds; non-text/UI minimum


def _get(cp, ref):
    sec, key = ref.split(":")
    return _rgb(cp.get(f"Colors:{sec}", key, fallback=None))


def check(path):
    cp = configparser.ConfigParser(strict=False)
    cp.optionxform = str
    try:
        cp.read(path)
    except Exception as e:
        return {"scheme": path, "error": f"parse failed: {e}", "ok": False, "rows": []}
    name = cp.get("General", "Name", fallback=path)
    rows, ok = [], True
    for label, fg_ref, bg_ref in PAIRS:
        fg, bg = _get(cp, fg_ref), _get(cp, bg_ref)
        if not fg or not bg:
            continue   # role absent in this scheme — skip, don't fail
        r = round(_ratio(fg, bg), 2)
        grade = "AAA" if r >= AAA else "AA" if r >= AA else "FAIL"
        if grade == "FAIL":
            ok = False
        rows.append({"pair": label, "ratio": r, "grade": grade,
                     "fg": ",".join(map(str, fg)), "bg": ",".join(map(str, bg))})
    for label, fg_ref, bg_ref in NONTEXT:
        fg, bg = _get(cp, fg_ref), _get(cp, bg_ref)
        if not fg or not bg:
            continue
        r = round(_ratio(fg, bg), 2)
        grade = "AAA" if r >= AAA else "AA" if r >= AA else "UI-OK" if r >= NONTEXT_MIN else "FAIL"
        if grade == "FAIL":
            ok = False
        rows.append({"pair": label, "ratio": r, "grade": grade,
                     "fg": ",".join(map(str, fg)), "bg": ",".join(map(str, bg))})
    return {"scheme": name, "ok": ok, "rows": rows}


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    if not args:
        print(__doc__); sys.exit(2)
    results = [check(p) for p in args]
    if as_json:
        print(json.dumps(results, indent=2))
        sys.exit(0 if all(r["ok"] for r in results) else 1)
    allok = True
    for res in results:
        allok = allok and res["ok"]
        print(f"\n=== {res['scheme']} === {'✓ all AA' if res['ok'] else '✗ has FAILs'}")
        if res.get("error"):
            print(f"  {res['error']}"); continue
        for row in res["rows"]:
            mark = {"AAA": "✓✓", "AA": "✓ ", "UI-OK": "✓ ", "FAIL": "✗ "}[row["grade"]]
            print(f"  {mark} {row['pair']:<18} {row['ratio']:>5}:1  [{row['grade']}]"
                  f"  ({row['fg']} on {row['bg']})")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
