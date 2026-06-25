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


def _mix(a, b, t):
    """Union mix(a, b, t): linear blend, t toward b (verified: t=0.5 is the midpoint)."""
    return tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3))


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
    # The focus ring is now UNIFIED to the window DecorationFocus on EVERY control (buttons
    # included), so verify that one ring colour clears 3:1 on the button surface too.
    ("focus ring on button", "Window:DecorationFocus", "Button:BackgroundNormal"),
    # engaged (hover) states — the indicator the style binds on :hovered must also clear 3:1
    ("hover ring (button)", "Button:DecorationHover", "Button:BackgroundNormal"),
    ("hover ring (window)", "Window:DecorationHover", "Window:BackgroundNormal"),
    ("hover ring (view)",   "View:DecorationHover",   "View:BackgroundNormal"),
]

AA, AAA, NONTEXT_MIN = 4.5, 7.0, 3.0   # normal-text thresholds; non-text/UI minimum

# COMPUTED pairs — colours the CSS SYNTHESISES with mix() that no raw scheme key carries, so the
# PAIRS/NONTEXT key-only checks above are blind to them. This is where the menu-shortcut-text
# regression hid (mix(.5) → ~2.95:1 in light). Each row resolves the same formula the CSS uses.
#   fg = ("mix", "Set:KeyA", "Set:KeyB", t)  -> _mix(A, B, t)   |  ("ref", "Set:Key")
#   kind = "text" (AA 4.5) | "nontext" (3:1) ; enforced=False prints but never fails the gate.
COMPUTED = [
    # (label, fg_expr, bg_ref, kind, enforced)
    ("menu shortcut text",  ("mix", "Window:BackgroundNormal", "Window:ForegroundNormal", 0.7),
     "Window:BackgroundNormal", "text", True),       # popup.css --menu-shortcut-text-color
    ("textfield placeholder", ("mix", "View:ForegroundNormal", "View:BackgroundNormal", 0.3),
     "View:BackgroundNormal", "text", True),          # text.css .placeholder
    ("secondary heading",   ("mix", "Window:BackgroundNormal", "Window:ForegroundNormal", 0.75),
     "Window:BackgroundNormal", "text", False),       # kirigami.css heading.secondary (alpha 0.75 ~ mix .75)
    # The "changed" field border is intentionally allowed below 3:1 — the non-colour edge-bar
    # (--changed-edge-width) carries the cue — so this row is informational, not enforced.
    ("changed-field border", ("ref", "Selection:BackgroundNormal"),
     "Window:BackgroundNormal", "nontext", False),
]


def _get(cp, ref):
    sec, key = ref.split(":")
    return _rgb(cp.get(f"Colors:{sec}", key, fallback=None))


def _resolve(cp, expr):
    """Resolve a COMPUTED fg expression to an rgb tuple (or None if a key is missing)."""
    if expr[0] == "ref":
        return _get(cp, expr[1])
    if expr[0] == "mix":
        a, b = _get(cp, expr[1]), _get(cp, expr[2])
        return _mix(a, b, expr[3]) if a and b else None
    return None


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
    for label, fg_expr, bg_ref, kind, enforced in COMPUTED:
        fg, bg = _resolve(cp, fg_expr), _get(cp, bg_ref)
        if not fg or not bg:
            continue
        r = round(_ratio(fg, bg), 2)
        floor = AA if kind == "text" else NONTEXT_MIN
        if r >= AAA:
            grade = "AAA"
        elif r >= AA:
            grade = "AA"
        elif kind == "nontext" and r >= NONTEXT_MIN:
            grade = "UI-OK"
        else:
            grade = "FAIL"
        if grade == "FAIL" and enforced:
            ok = False
        # informational rows print their grade with a '~' marker and never fail the gate
        rows.append({"pair": label + ("" if enforced else " ~"), "ratio": r,
                     "grade": grade, "fg": ",".join(map(str, fg)), "bg": ",".join(map(str, bg))})
    # Vacuous-pass guard: a scheme that matched NO pairings must NOT report "✓ all AA".
    if not rows:
        return {"scheme": name, "error": "no pairings evaluated (empty/foreign scheme?)",
                "ok": False, "rows": []}
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
            info = row["pair"].endswith(" ~")   # informational (non-enforced) computed row
            mark = ("·  " if info and row["grade"] == "FAIL"
                    else {"AAA": "✓✓", "AA": "✓ ", "UI-OK": "✓ ", "FAIL": "✗ "}[row["grade"]])
            print(f"  {mark} {row['pair']:<18} {row['ratio']:>5}:1  [{row['grade']}]"
                  f"  ({row['fg']} on {row['bg']})")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
