#!/usr/bin/env python3
# test_g7_contrast.py — validates the pure WCAG functions in g7_contrast.py against
# known reference pairs, and asserts the G7 gate keeps reproducing the design-doc's
# measured ratios. Stdlib-only; run with `python3 test_g7_contrast.py`.
#
# SPDX-License-Identifier: MIT
import contextlib
import io

import g7_contrast as g


def approx(a, b, tol=0.01):
    return abs(a - b) <= tol


def test_black_white_is_21():
    # The canonical maximum WCAG contrast.
    r = g.contrast_ratio("#000000", "#ffffff")
    assert approx(r, 21.0), f"black/white expected 21:1, got {r:.4f}"
    # order-independent
    assert approx(g.contrast_ratio("#ffffff", "#000000"), 21.0)


def test_identical_colors_is_1():
    assert approx(g.contrast_ratio("#7a8090", "#7a8090"), 1.0)
    assert approx(g.contrast_ratio("#ffffff", "#ffffff"), 1.0)


def test_luminance_endpoints():
    assert approx(g.relative_luminance("#000000"), 0.0)
    assert approx(g.relative_luminance("#ffffff"), 1.0)


def test_mid_gray_pair_known():
    # WebAIM reference: #808080 on #ffffff ~= 3.95:1 (mid grey on white).
    r = g.contrast_ratio("#808080", "#ffffff")
    assert approx(r, 3.95, tol=0.03), f"#808080/#fff expected ~3.95:1, got {r:.4f}"
    # #777 on #fff ~= 4.48:1 (a well-known "just under AA" grey).
    r2 = g.contrast_ratio("#777777", "#ffffff")
    assert approx(r2, 4.48, tol=0.03), f"#777/#fff expected ~4.48:1, got {r2:.4f}"


def test_rgb_tuple_accepted():
    # 0..255 tuple form must match the hex form.
    assert approx(g.contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0)
    assert approx(g.relative_luminance((255, 255, 255)),
                  g.relative_luminance("#ffffff"))


def test_short_hex_accepted():
    assert approx(g.contrast_ratio("#000", "#fff"), 21.0)


def test_gate_reproduces_doc_flow_tray():
    # The design-doc's worst-case warm-on-warm: #ff9957 over the Flow bloom = 3.07:1.
    r = g.contrast_ratio(g.WARM_TOKEN, g.FIELD_WARM_FLOW)
    assert approx(r, 3.07, tol=0.02), f"Flow tray expected ~3.07:1, got {r:.4f}"


def test_gate_reproduces_doc_web_controls():
    # Allow ~6.06, Cancel ~5.32, body ~12.4 on the effective navy body.
    assert approx(g.contrast_ratio(g.WEB_ALLOW, g.NAVY_BODY), 6.06, tol=0.1)
    assert approx(g.contrast_ratio(g.WEB_CANCEL, g.NAVY_BODY), 5.32, tol=0.1)
    assert approx(g.contrast_ratio(g.BODY_LABEL, g.NAVY_BODY), 12.4, tol=0.12)


def test_gate_passes_after_fallbacks():
    # G7's payoff: the fallbacks ARE applied (F1b web caption #7a8090->#878c9b, F2a tray text
    # ->#e6e9f0 with warmth moved to the ring), so the gate now PASSES — every required surface
    # clears WCAG AA on BOTH Hills and Flow. If this regresses, a token edit broke the gate.
    with contextlib.redirect_stdout(io.StringIO()):  # keep the test log clean
        rc = g.main()
    assert rc == 0, "G7 gate must PASS (exit 0) now that the F1b + F2a fallbacks have landed"


def test_known_fallbacks_clear_aa():
    # F2b opaque chip (#12141c) must make even the bright warm token clear AA.
    assert g.contrast_ratio(g.WARM_TOKEN, g.INST_BASE) >= 4.5
    # F1a demote-to-muted must clear AA on the glass card.
    assert g.contrast_ratio(g.INST_MUTED, g.GLASS_CARD_EFF) >= 4.5


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"ok    {t.__name__}")
            passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
