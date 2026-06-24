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
    ok_c = _try_set(node, "collection", mpc)
    ok_p = _try_set(node, "parameter_name", unreal.Name(param_name))
    log("collection-param {}: collection_set={} name_set={}".format(param_name, ok_c, ok_p))
    return node


def _connect(a, a_out, b, b_in):
    """connect_material_expressions, but CHECK the return — a silent False here leaves the
    downstream input at 0, which is exactly the 'tap authored but no reaction' failure. Tries the
    empty default output first, then a couple of common scalar output names if that fails."""
    if MEL.connect_material_expressions(a, a_out, b, b_in):
        return True
    # CollectionParameter / some nodes expose the scalar under a named output, not "".
    for alt in ("", "Out", "Result", a_out):
        if alt != a_out and MEL.connect_material_expressions(a, alt, b, b_in):
            log("connect used alt output '{}' → {}.{}".format(alt, b.get_class().get_name(), b_in))
            return True
    msg = "CONNECT FAILED: {}.'{}' -> {}.{}".format(a.get_class().get_name(), a_out, b.get_class().get_name(), b_in)
    log(msg)
    _FAIL.append(msg)
    return False


# The slab's authored emissive whisper — MUST stay in sync with indigo_channel_setup.py
# `SLAB_EMIS` (the indigo whisper-lift on the blades). The reactive emissive chain rebuilds
# from this exact value so idle (Warm=0, Desat=0) is byte-identical to the static scene.
SLAB_EMIS = unreal.LinearColor(0.008, 0.010, 0.022, 1.0)

# The needs-you dawn tint (ADR-0030 D8 — warm reserved for needs_you), PRE-SCALED to a calm
# ceiling so `Warm=1` lifts the blades a perceptible-but-not-flaring amount under the −3 exposure
# (≈ 0.10 × the #E8B27A dawn hue). Tunable; the art-director owns the final magnitude [VERIFY-LIVE].
WARM_TINT = unreal.LinearColor(0.091, 0.070, 0.048, 1.0)


def wire_slab_reactive_emissive(mpc):
    """Tap BOTH `Desat` (a calm dim) and `Warm` (the needs-you dawn) into the slab/blade
    emissive — the prominent dark silhouettes — and ACTUALLY CONNECT them to MP_EMISSIVE_COLOR.

        emissive = (SLAB_EMIS + Warm * WARM_TINT) * (1 - 0.5*Desat)

    Idle (`Warm=0, Desat=0`) ⇒ emissive = SLAB_EMIS ⇒ byte-identical to the static scene
    (ADR-0030 D4). A `needs_you` warms the blades; a snag dims them — both calm, never red.

    The graph is REBUILT and the Emissive Color input is RE-CONNECTED to the new chain (rather
    than the fragile "find the existing node and splice" surgery the earlier draft deferred): a
    material property has ONE input, so connecting the new chain replaces the static constant.

    `Desat` is the snag/desaturate half; `Fog` (the other snag-raised axis) is a SEPARATE MPC
    scalar a fog material reads — both pushed by `agentosd rc`, never a cvar/throttle (ADR-0030 D3).
    The `Backlight` (rake brightness) is a Light Function Material on the light, authored separately."""
    mat_path = MAT_DIR + "/M_AgentOS_Slab"
    if not unreal.EditorAssetLibrary.does_asset_exist(mat_path):
        log("slab material absent ({}); run indigo_channel_setup.py first".format(mat_path))
        _FAIL.append("M_AgentOS_Slab missing — reactive taps need the static build")
        return
    mat = unreal.EditorAssetLibrary.load_asset(mat_path)
    if mat is None:
        _FAIL.append("could not load slab material for tapping")
        return

    # --- dim = 1 - 0.5*Desat  (Desat=0 -> 1.0, unchanged) ---
    desat = _collection_param(mat, mpc, "Desat", -760, 360)
    half = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -760, 460)
    _try_set(half, "r", 0.5)
    scaled = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -600, 400)
    _connect(desat, "", scaled, "A")
    _connect(half, "", scaled, "B")
    one = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -600, 500)
    _try_set(one, "r", 1.0)
    dim = MEL.create_material_expression(mat, unreal.MaterialExpressionSubtract, -440, 440)
    _connect(one, "", dim, "A")
    _connect(scaled, "", dim, "B")

    # --- warm_add = Warm * WARM_TINT  (Warm=0 -> 0, no add) ---
    warm = _collection_param(mat, mpc, "Warm", -760, 0)
    tint = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -760, 100)
    _try_set(tint, "constant", WARM_TINT)
    warm_add = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -600, 40)
    _connect(warm, "", warm_add, "A")
    _connect(tint, "", warm_add, "B")

    # --- emis_warmed = SLAB_EMIS + warm_add ---
    base = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -600, 160)
    _try_set(base, "constant", SLAB_EMIS)
    warmed = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, -440, 100)
    _connect(base, "", warmed, "A")
    _connect(warm_add, "", warmed, "B")

    # --- emis_out = emis_warmed * dim  -> Emissive Color (REPLACES the static constant) ---
    emis_out = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -260, 240)
    _connect(warmed, "", emis_out, "A")
    _connect(dim, "", emis_out, "B")
    if not MEL.connect_material_property(emis_out, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        _FAIL.append("could not re-connect slab emissive to the reactive chain")
        return
    log("slab reactive emissive authored: (SLAB_EMIS + Warm*tint)*(1-0.5*Desat); idle == SLAB_EMIS")

    MEL.layout_material_expressions(mat)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(mat_path)


# ---------------------------------------------------------------------------
# 3. Post-process GRADE — the VISIBLE reactive lever in a fog-dominated scene.
#    A surface tap on the dark blades is occluded by the volumetric fog (the blades are
#    occluders INSIDE the cyan fog; their warm surface is washed by inscatter in front). A
#    full-screen post grade tints the FINAL image (fog + rake included) — unmistakably visible,
#    still MPC-driven (§B SetScalarParameterValue), idle == identity.
# ---------------------------------------------------------------------------
GRADE_NAME = "M_AgentOS_ReactiveGrade"
GRADE_PATH = MAT_DIR + "/" + GRADE_NAME


def wire_post_grade(mpc):
    """Author M_AgentOS_ReactiveGrade (MD_PostProcess): out = SceneColor * lerp(white, warmMul, Warm)
    → Emissive. Warm=0 ⇒ ×white ⇒ scene unchanged (idle-identical); Warm=1 ⇒ ×(1.6,1.0,0.55) ⇒ the
    whole image warms and the cyan is pulled down. Then attach it as a PostProcessVolume blendable."""
    if unreal.EditorAssetLibrary.does_asset_exist(GRADE_PATH):
        unreal.EditorAssetLibrary.delete_asset(GRADE_PATH)
    mat = _assets.create_asset(GRADE_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        _FAIL.append("could not create post-grade material")
        return
    if not _try_set(mat, "material_domain", unreal.MaterialDomain.MD_POST_PROCESS):
        _FAIL.append("could not set MD_POST_PROCESS on the grade material")

    scene = MEL.create_material_expression(mat, unreal.MaterialExpressionSceneTexture, -800, 0)
    _try_set(scene, "scene_texture_id", unreal.SceneTextureId.PPI_POST_PROCESS_INPUT0)

    # ---- the reactive chain — ALL the visible levers, idle-identical at the MPC defaults ----
    # graded = ((SceneColor × Backlight) + (Fog-1)·coolHaze) × lerp(white, warm, Warm) + Air·airStir
    #   Backlight (×, default 1) = busy lift;  Fog (×→ -1, default 1 ⇒ +0) = snag cool haze;
    #   Warm (+, default 0) = needs-you dawn;  Air (+, default 0) = the WINDOW-DRAG breath.
    # At defaults (Backlight=1, Fog=1, Warm=0, Air=0) ⇒ graded == SceneColor (ADR-0030 D4 idle-identical).
    bl = _collection_param(mat, mpc, "Backlight", -800, 200)
    lit = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -560, 40)
    _connect(scene, "Color", lit, "A")
    _connect(bl, "", lit, "B")

    fog = _collection_param(mat, mpc, "Fog", -800, 320)
    one_f = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -800, 420)
    _try_set(one_f, "r", 1.0)
    fog_m1 = MEL.create_material_expression(mat, unreal.MaterialExpressionSubtract, -640, 340)
    _connect(fog, "", fog_m1, "A")
    _connect(one_f, "", fog_m1, "B")
    haze_tint = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -800, 500)
    _try_set(haze_tint, "constant", unreal.LinearColor(0.05, 0.07, 0.11, 1.0))  # cool snag haze lift
    haze = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -480, 400)
    _connect(fog_m1, "", haze, "A")
    _connect(haze_tint, "", haze, "B")
    hazed = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, -320, 120)
    _connect(lit, "", hazed, "A")
    _connect(haze, "", hazed, "B")

    warm = _collection_param(mat, mpc, "Warm", -800, 620)
    white = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -800, 680)
    _try_set(white, "constant", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    warm_mul = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -800, 760)
    _try_set(warm_mul, "constant", unreal.LinearColor(1.6, 1.0, 0.55, 1.0))  # warm shift (cuts cyan)
    tint = MEL.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, -560, 680)
    _connect(white, "", tint, "A")
    _connect(warm_mul, "", tint, "B")
    _connect(warm, "", tint, "Alpha")
    warmed = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -120, 240)
    _connect(hazed, "", warmed, "A")
    _connect(tint, "", warmed, "B")

    air = _collection_param(mat, mpc, "Air", -800, 880)
    air_tint = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -800, 960)
    _try_set(air_tint, "constant", unreal.LinearColor(0.04, 0.08, 0.12, 1.0))  # cool window-drag breath
    air_add = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -560, 920)
    _connect(air, "", air_add, "A")
    _connect(air_tint, "", air_add, "B")
    graded = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, 120, 320)
    _connect(warmed, "", graded, "A")
    _connect(air_add, "", graded, "B")
    if not MEL.connect_material_property(graded, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        _FAIL.append("could not connect post-grade to emissive")
        return
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(GRADE_PATH)
    log("post-grade material authored: SceneColor * lerp(white, warm, Warm)")

    # --- attach as a PostProcessVolume blendable in the open level, then save the level ---
    try:
        eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actors = eas.get_all_level_actors()
    except Exception:  # noqa: BLE001
        actors = unreal.EditorLevelLibrary.get_all_level_actors()
    ppv = next((a for a in actors if isinstance(a, unreal.PostProcessVolume)), None)
    if ppv is None:
        # No PPV in the level → spawn an unbound one. A missing PPV makes the whole grade invisible —
        # the likely cause of the prior silent no-reaction. Tolerant across the editor-actor API.
        try:
            ppv = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
                unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
        except Exception:  # noqa: BLE001
            try:
                ppv = unreal.EditorLevelLibrary.spawn_actor_from_class(
                    unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
            except Exception:  # noqa: BLE001
                ppv = None
        if ppv is None:
            _FAIL.append("no PostProcessVolume and could not spawn one for the grade")
            return
        _try_set(ppv, "actor_label", "AgentOS_ReactivePPV")
        log("spawned an unbound PostProcessVolume for the reactive grade")
    _try_set(ppv, "unbound", True)  # apply everywhere, not just inside the volume
    settings = ppv.get_editor_property("settings")
    wb = unreal.WeightedBlendable()
    wb.set_editor_property("weight", 1.0)
    wb.set_editor_property("object", mat)
    blendables = unreal.WeightedBlendables()
    blendables.set_editor_property("array", [wb])
    settings.set_editor_property("weighted_blendables", blendables)
    ppv.set_editor_property("settings", settings)
    log("post-grade attached to PostProcessVolume '{}' (unbound)".format(ppv.get_actor_label()))
    try:
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()
    except Exception:  # noqa: BLE001
        unreal.EditorLevelLibrary.save_current_level()
    log("level saved with the reactive grade")


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
        wire_slab_reactive_emissive(mpc)
    except Exception as exc:  # noqa: BLE001
        import traceback
        unreal.log_warning("[AgentOS indigo_reactive] slab tap skipped: {}".format(exc))
        for line in traceback.format_exc().splitlines():
            unreal.log_warning("[AgentOS indigo_reactive] " + line)

    try:
        wire_post_grade(mpc)
    except Exception as exc:  # noqa: BLE001
        import traceback
        unreal.log_warning("[AgentOS indigo_reactive] post-grade skipped: {}".format(exc))
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
