#!/usr/bin/env python3
"""lint-css.py — deterministic structural lint for the Aurora Union CSS style ("code disposes").

Two checks the old apply.sh focus grep could not do:

  1. TRANSLUCENT-OUTLINE scan (hard fail). A focus outline must use the SOLID --focus-ring-color
     (via --focus-outline / --button-focus), never a translucent token (--focus-color and any
     other modify-color(... set-alpha ...) value), which composites below WCAG 1.4.11 3:1. The old
     grep only caught the exact string `outline:...--focus-color`; this catches outline-color: too,
     case-insensitively, plus any alpha-bearing token used as an outline.

  2. FOCUS-COVERAGE assertion (hard fail). Every interactive widget that styles a :hovered or
     :pressed state must ALSO have a visible keyboard focus indicator (:visual-focus / :active-focus
     / an outline) somewhere in the style — otherwise a keyboard user can't see where they are
     (WCAG 2.4.7). This is the gate that keeps the focus-ring coverage from silently regressing the
     way dial/tabbutton/itemdelegate/card/chip/navigationtabbutton had drifted.

Usage:  lint-css.py <style-dir>     # the dir holding the per-widget *.css (default: ../css/styles/aurora)
Exit 0 iff clean; 1 otherwise (so apply.sh can gate on it). Pure stdlib.
"""
from __future__ import annotations
import os, re, sys

# Widgets that legitimately style hover/pressed but are NOT keyboard-focus targets (or use a
# different focus channel), so they are exempt from the coverage assertion. Each is a deliberate,
# documented exemption — keep this list honest.
FOCUS_EXEMPT = {
    "scrollbar",        # drag handle, not a tab stop
    "menuitem",         # keyboard "current" item uses :highlighted, not :visual-focus (see popup.css)
    "menubaritem",      # opened via accelerator/click; highlight is the cue
    "progressbar",      # non-interactive
    "busyindicator",    # non-interactive
    "tumbler",          # uses :highlighted
    "toolseparator",    # non-interactive
    "splitview",        # the handle is a drag target, not a tab stop
    "pageindicatordelegate",  # decorative dots
}

STATE_RE = re.compile(r":(hovered|pressed|visual-focus|active-focus)\b")
# leading widget type tokens of a top-level selector (before any :,.,[,>,&,{,whitespace)
TYPE_RE = re.compile(r"^\s*([a-z][a-z0-9]*)")


def top_level_blocks(css: str):
    """Yield (selector, body) for each brace-depth-0 rule block. Strips /* */ comments first."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i, n, depth, sel_start = 0, len(css), 0, 0
    sel = ""
    while i < n:
        c = css[i]
        if c == "{":
            if depth == 0:
                sel = css[sel_start:i].strip()
                body_start = i + 1
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                yield sel, css[body_start:i]
                sel_start = i + 1
        i += 1


def widget_types(selector: str):
    """Every widget type token a top-level selector references — across all comma parts AND all
    simple-selector segments within a part. So `slider:visual-focus > handle` yields {slider,
    handle}: the state on `slider` is attributed to slider, not only to the trailing `handle`."""
    out = set()
    for part in selector.split(","):
        # split each part on descendant/child/sibling combinators into simple selectors
        for seg in re.split(r"[ >+~]+", part.strip()):
            seg = seg.strip()
            if not seg or seg.startswith("&"):
                continue
            m = TYPE_RE.match(seg)
            if m:
                out.add(m.group(1))
    return out


def lint(style_dir: str):
    errors, warnings = [], []
    has_state = {}      # widget -> set of states seen across ALL blocks/files
    files = sorted(f for f in os.listdir(style_dir) if f.endswith(".css"))
    for fn in files:
        path = os.path.join(style_dir, fn)
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        # 1. translucent-outline scan (line-based, comments stripped per line is enough here)
        for ln, line in enumerate(raw.splitlines(), 1):
            code = re.sub(r"/\*.*?\*/", "", line)
            if re.search(r"\boutline(-color)?\s*:", code, re.I):
                if re.search(r"--focus-color\b", code) or re.search(r"set-alpha", code, re.I):
                    errors.append(f"{fn}:{ln}: translucent focus outline (must be the SOLID "
                                  f"--focus-ring-color / --focus-outline): {line.strip()}")
        # Fail-closed before trusting the brace parser: unbalanced braces desync top_level_blocks,
        # which can make the focus-coverage assertion below pass VACUOUSLY on a malformed file.
        # (The line-based outline scan above is brace-independent, so it still ran.)
        nb = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        if nb.count("{") != nb.count("}"):
            errors.append(f"{fn}: unbalanced braces ({nb.count('{')} open vs {nb.count('}')} close) "
                          f"— CSS malformed; focus coverage cannot be trusted. Fix the file.")
            continue
        # 2. accumulate per-widget state coverage
        for sel, body in top_level_blocks(raw):
            wtypes = widget_types(sel)
            if not wtypes:
                continue
            states = set(STATE_RE.findall(sel)) | set(STATE_RE.findall(body))
            # an `outline:` anywhere in the block (even without a state pseudo on this selector,
            # e.g. `slider:visual-focus > handle {}` is its OWN block whose selector carries the state)
            if re.search(r"\boutline(-color)?\s*:", body, re.I):
                states.add("visual-focus")
            for w in wtypes:
                has_state.setdefault(w, set()).update(states)

    # coverage assertion: interactive (hover/pressed) ⇒ must have a focus channel
    FOCUS = {"visual-focus", "active-focus"}
    for w, states in sorted(has_state.items()):
        if w in FOCUS_EXEMPT:
            continue
        interactive = states & {"hovered", "pressed"}
        focusable = states & FOCUS
        if interactive and not focusable:
            errors.append(f"widget '{w}': styles {sorted(interactive)} but has NO focus indicator "
                          f"(:visual-focus/:active-focus/outline) — keyboard focus invisible "
                          f"(WCAG 2.4.7). Add a focus ring or add '{w}' to FOCUS_EXEMPT with reason.")
    return errors, warnings


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    here = os.path.dirname(os.path.abspath(__file__))
    style_dir = args[0] if args else os.path.join(here, "..", "css", "styles", "aurora")
    if not os.path.isdir(style_dir):
        print(f"✗ no such style dir: {style_dir}", file=sys.stderr); sys.exit(2)
    errors, warnings = lint(os.path.abspath(style_dir))
    for w in warnings:
        print(f"  ! {w}")
    for e in errors:
        print(f"  ✗ {e}")
    if errors:
        print(f"✗ aurora CSS lint: {len(errors)} error(s)")
        sys.exit(1)
    print("✓ aurora CSS lint: focus coverage + solid-outline checks pass")
    sys.exit(0)


if __name__ == "__main__":
    main()
