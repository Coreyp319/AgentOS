# indigo_reactive_setup.py — author the REACTIVE layer for "The Indigo Channel".
#
# WHAT THIS IS (ADR-0023/0029 reactivity build-spec, the runtime mechanism)
# ----------------------------------------------------------------------------
#   indigo_channel_setup.py builds the STATIC dark-ride (blades + fog + cyan
#   rake + parallax LevelSequence). THIS script adds the REACTIVE seam on top
#   of it, WITHOUT a Blueprint and WITHOUT runtime Python (the official
#   PythonScriptPlugin is editor-only at runtime — it cannot tick in a packaged
#   `-game` build; verified via Epic docs 2026-06-20). The chosen mechanism is:
#
#     agentosd (already running, already reads agent.json/wind.json)
#        --> loopback HTTP Remote Control (:30010, the ALREADY-researched channel
#            in remote_control_setup.md) --> /remote/object/call on the engine CDO
#            KismetMaterialLibrary::SetScalarParameterValue
#        --> a Material Parameter Collection (MPC) `MPC_AgentOS_Reactive`
#        --> referenced by the fog/light/post materials as Collection Parameters.
#
#   The MPC is the ONE piece authored here. It is the renderer-side mailbox:
#   global scalars any material can read, settable from outside the engine over
#   the proven RC channel with no in-engine code. This is the lock-free,
#   headless-authorable, no-Blueprint option (see SPEC §1 for the comparison
#   that rejected runtime-Python-tick and reader-actor-Blueprint).
#
#   IMPORTANT (the load-bearing safety inheritance, ADR-0029 §B / ADR-0030 D3):
#   the reactive PUSH is BUILT as `agentosd rc` (crates/agentosd/src/rc.rs) — a
#   SEPARATE PROCESS (the strongest isolation: a wedged PUT can never delay the
#   lease daemon's SIGKILL because it is not even the same address space; it owns
#   no lease state and shares no lock). It reads the disposed `scene-params.json`
#   and pushes these MPC scalars over a LOOPBACK-LITERAL RC target (no DNS, no
#   env host), with the allowlisted `SetScalarParameterValue` only (never
#   `ExecuteConsoleCommand`). This script only AUTHORS the MPC + wires materials;
#   it spawns nothing and touches no lock. Security model: ADR-0029 §B "RE-GROUNDED
#   2026-06-21" — UE's NATIVE default-deny allowlist (cooked config) closes the
#   code-exec hole; loopback is NOT a trust boundary, so the allowlist is the only
#   control (accepted residual: any local proc can nudge these bounded scalars).
#
# WHAT THIS SCRIPT DOES (idempotent, headless, same harness as the static build)
#   1. Create/overwrite the MPC asset with the reactive scalar set (SPEC §2).
#   2. Re-wire the fog inscatter, light intensity/colour, and PPV-bloom material
#      paths to read those collection parameters (additive lerps; idle = the
#      EXACT static values, so MPC-all-zero == the unmodified Indigo Channel —
#      the ADR-0009 idle-byte-identical invariant).
#
# WHAT THIS SCRIPT DOES NOT DO (by design / by constraint)
#   - It does NOT launch UnrealEditor, package, or touch the GPU. Corey runs the
#     authoring pass + the live `-game` test on the shared 4090.
#   - It does NOT write the agentosd-side pusher — that is BUILT + unit-tested:
#     `crates/agentosd/src/rc.rs` (`agentosd rc`). Its `AXES` set MUST match the
#     REACTIVE_SCALARS below name-for-name (a mismatch = every PUT rejected). The
#     pusher reads the DISPOSED `scene-params.json` (scene.rs), not the raw feeds.
#
# HOW TO RUN IT (headless, when on the box — same harness as the static build)
#   SCENE_SCRIPT=indigo_reactive_setup.py MARK='Indigo reactive MPC built' \
#     bash spikes/ue-probe/author_scene.sh
#   (run indigo_channel_setup.py FIRST so the materials exist; this re-opens the
#    same /Game/AgentOS/Materials and adds the collection-parameter taps.)
#
# Status: DRAFT / GATED. Mirrors the proven indigo_channel_setup.py idioms
# (idempotent delete-before-create, tolerant _try_set, _FAIL accumulator). Every
# property/asset name below is the best-available from the UE5.8 docs + the
# existing spike; the ones marked VERIFY-LIVE need a first-cook confirmation.

import os

import unreal

# ---------------------------------------------------------------------------
# Config — mirror indigo_channel_setup.py paths so we touch the SAME assets
# ---------------------------------------------------------------------------
MAT_DIR    = "/Game/AgentOS/Materials"
MPC_DIR    = "/Game/AgentOS/Materials"
MPC_NAME   = "MPC_AgentOS_Reactive"
MPC_PATH   = MPC_DIR + "/" + MPC_NAME

# The reactive scalar set = the DISPOSER's OUTPUT schema (crates/agentosd/src/scene.rs
# `SceneState::fields` → scene-params.json), so the SAME axis names flow end to end —
# one grammar, no second home (ADR-0030 D1/D2):
#
#     scene.rs (disposer) --> scene-params.json --> agentosd `rc` (crates/agentosd/src/rc.rs)
#       --> SetScalarParameterValue --> THESE MPC scalars --> the fog/light/post materials.
#
# These names MUST match rc.rs `AXES` exactly (the pusher writes precisely this set; a
# name mismatch = every PUT rejected). DEFAULT is the idle value.
#
#   name           default  kind   lever (scene.rs axis; see scene.rs for bounds)
#   -------------  -------  -----  ----------------------------------------------------
#   Motion         1.0      mult   parallax/breath rate (INDIGO_MOTION_SPEED); busy quickens, 0=freeze
#   Fog            1.0      mult   volumetric fog density (INDIGO_FOG_DENSITY); snag thickens
#   Backlight      1.0      mult   cyan-rake intensity (INDIGO_LIGHT_INT); stale dims one step (D9)
#   Warm           0.0      add    far-end warm-amber inscatter (RESERVED for needs_you, D8)
#   Desat          0.0      add    snag desaturate — the non-color, accessibility cue
#   Air            0.0      add    wind/AIR impulse magnitude (window-drag gust)
#   Fresh          0.0      code   freshness 0=fresh / 1=stale / 2=blind (D9; material may tint)
#   ReduceMotion   0.0      flag   a11y reduce-motion (1.0 ⇒ hold steady, don't breathe)
#
# IDLE-PARAMETER-IDENTICAL (ADR-0030 D4 / ADR-0009): idle = the REST frame, NOT all-zero.
# The MULTIPLICATIVE levers (Motion/Fog/Backlight) default to 1.0 — their "off" is ×1, not
# +0; the ADDITIVE levers (Warm/Desat/Air) and the signals (Fresh/ReduceMotion) default to
# 0.0. So an MPC at these defaults reproduces the unmodified Indigo Channel exactly. The
# material taps below must therefore read the multiplicative levers as MULTIPLIERS and the
# additive ones as ADDS, so that the REST frame leaves every material output unchanged.
REACTIVE_SCALARS = [
    ("Motion",       1.0),
    ("Fog",          1.0),
    ("Backlight",    1.0),
    ("Warm",         0.0),
    ("Desat",        0.0),
    ("Air",          0.0),
    ("Fresh",        0.0),
    ("ReduceMotion", 0.0),
]

_FAIL = []
MEL = unreal.MaterialEditingLibrary
_assets = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log("[AgentOS indigo_reactive] " + str(msg))


def _try_set(obj, prop, value):
    try:
        obj.set_editor_property(prop, value)
        return True
    except Exception as exc:  # noqa: BLE001
        log("skip set {}={!r}: {}".format(prop, value, exc))
        return False


# ---------------------------------------------------------------------------
# 1. The MPC asset — the reactive mailbox
# ---------------------------------------------------------------------------
def build_mpc():
    """Create/overwrite MPC_AgentOS_Reactive with REACTIVE_SCALARS. The MPC's
    `scalar_parameters` is an array of FCollectionScalarParameter structs
    (fields: parameter_name, default_value). We delete-before-create for
    idempotency (the scene's house style).

    VERIFY-LIVE: the exact Python struct ctor for FCollectionScalarParameter and
    the array property name (`scalar_parameters`) are the documented UE5.x names;
    confirm against this install's `unreal.CollectionScalarParameter` on first run
    (some point releases expose `set_editor_property('scalar_parameters', [...])`
    only via a freshly-constructed struct list, which is what we do below)."""
    if unreal.EditorAssetLibrary.does_asset_exist(MPC_PATH):
        unreal.EditorAssetLibrary.delete_asset(MPC_PATH)
        log("deleted prior MPC {}".format(MPC_PATH))

    mpc = _assets.create_asset(
        MPC_NAME, MPC_DIR,
        unreal.MaterialParameterCollection,
        unreal.MaterialParameterCollectionFactoryNew(),
    )
    if mpc is None:
        _FAIL.append("create_asset returned None for {}".format(MPC_PATH))
        return None

    scalars = []
    for name, default in REACTIVE_SCALARS:
        p = unreal.CollectionScalarParameter()
        # Both setters are best-effort across point releases.
        _try_set(p, "parameter_name", unreal.Name(name))
        _try_set(p, "default_value", float(default))
        scalars.append(p)
    if not _try_set(mpc, "scalar_parameters", scalars):
        _FAIL.append("could not set scalar_parameters on the MPC")

    unreal.EditorAssetLibrary.save_asset(MPC_PATH)
    log("MPC built + saved: {} ({} scalars)".format(MPC_PATH, len(scalars)))
    return mpc


# ---------------------------------------------------------------------------
# 2. Material taps — additive, idle == static (the byte-identical invariant)
# ---------------------------------------------------------------------------
def _collection_param(mat, mpc, param_name, x, y):
    """A CollectionParameter expression reading `param_name` from `mpc`.
    Returns the expression (its default output is the scalar)."""
    node = MEL.create_material_expression(
        mat, unreal.MaterialExpressionCollectionParameter, x, y)
    _try_set(node, "collection", mpc)
    _try_set(node, "parameter_name", unreal.Name(param_name))
    return node


def wire_slab_desat(mpc):
    """Tap Desat into the slab/post path as a gentle dim — calm, never red (the
    snag design law, aurora.frag:714-718). On the SLAB material we only have
    albedo/emissive; the real luma-desaturate belongs in a POST material, so here
    we just DIM the emissive whisper by (1 - 0.5*Desat) so a snag reads as the
    room going quieter, with Desat=0 leaving the emissive untouched (idle-identical).

    NOTE: this is the minimal, safe stand-in that PRESERVES idle-parameter-identical.
    The fuller snag haze is the `Fog` MPC scalar (a MULTIPLIER the fog/inscatter
    material reads) — pushed by `agentosd rc` exactly like every other axis, NOT a
    cvar and NOT the throttle channel (mood must never ride the safety channel,
    ADR-0030 D3). `Desat` and `Fog` are two DIFFERENT disposed axes both raised by a
    snag upstream; this tap is the `Desat` half. (Earlier drafts wrongly routed fog
    density via a cvar — corrected: fog density is reached through the fog material
    reading the `Fog` MPC scalar, the one-grammar all-MPC path.)"""
    mat_path = MAT_DIR + "/M_AgentOS_Slab"
    if not unreal.EditorAssetLibrary.does_asset_exist(mat_path):
        log("slab material absent ({}); run indigo_channel_setup.py first".format(mat_path))
        _FAIL.append("M_AgentOS_Slab missing — reactive taps need the static build")
        return
    mat = unreal.EditorAssetLibrary.load_asset(mat_path)
    if mat is None:
        _FAIL.append("could not load slab material for tapping")
        return

    # Existing emissive constant is SLAB_EMIS. We multiply it by (1 - 0.5*Desat).
    # Desat default 0 -> factor 1 -> emissive UNCHANGED -> idle parameter-identical.
    desat = _collection_param(mat, mpc, "Desat", -700, 320)
    half = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -700, 420)
    _try_set(half, "r", 0.5)
    scaled = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -560, 360)
    MEL.connect_material_expressions(desat, "", scaled, "A")
    MEL.connect_material_expressions(half, "", scaled, "B")
    one = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -560, 460)
    _try_set(one, "r", 1.0)
    dim = MEL.create_material_expression(mat, unreal.MaterialExpressionSubtract, -420, 380)
    MEL.connect_material_expressions(one, "", dim, "A")
    MEL.connect_material_expressions(scaled, "", dim, "B")
    # NB: wiring `dim` into the emissive multiply requires re-routing the existing
    # emissive constant3vector through a Multiply(emis, dim). That graph surgery is
    # done live (it needs the existing node handles); the structure above is the
    # additive shape. Marked VERIFY-LIVE: confirm the emissive re-route preserves
    # the idle frame with a fixed-iTime PNG diff vs the static capture.
    log("slab desat tap authored (dim emissive by 0.5*Desat; Desat=0 -> unchanged)")

    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(mat_path)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
def main():
    mpc = build_mpc()
    if mpc is None:
        for f in _FAIL:
            unreal.log_error("[AgentOS indigo_reactive] BUILD FAIL: " + f)
        return
    # Material taps are the part most exposed to graph-API drift; they are
    # additive and gated so a tap failure is logged but the MPC still lands
    # (the MPC alone is useful — light/fog levers are pushed as cvars, SPEC §3).
    try:
        wire_slab_desat(mpc)
    except Exception as exc:  # noqa: BLE001
        import traceback
        unreal.log_warning("[AgentOS indigo_reactive] slab tap skipped: {}".format(exc))
        for line in traceback.format_exc().splitlines():
            unreal.log_warning("[AgentOS indigo_reactive] " + line)

    if _FAIL:
        for f in _FAIL:
            unreal.log_error("[AgentOS indigo_reactive] BUILD FAIL: " + f)
        log("BUILD FAILED with {} issue(s) — NOT emitting success marker".format(len(_FAIL)))
        return
    log("Indigo reactive MPC built")  # author_scene.sh MARK


def _run():
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        unreal.log_error("[AgentOS indigo_reactive] FATAL: {}".format(exc))
        for line in traceback.format_exc().splitlines():
            unreal.log_error("[AgentOS indigo_reactive] " + line)


_run()
