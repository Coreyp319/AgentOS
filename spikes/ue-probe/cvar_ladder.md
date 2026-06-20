# UE 5.8 throttle ladder — calm-wallpaper Lumen resident

Three rungs for a live UnrealEditor (Lumen) scene sharing the RTX 4090 with the
desktop. Each rung is a single console-command string ready to drop verbatim into
`-ExecCmds="..."` at launch (commands are comma-separated). `launch_offscreen.sh
--exec "<rung>"` does exactly that.

All cvar / scalability-group names below were verified against this exact UE 5.8
install's `Engine/Config/BaseScalability.ini` (every `sg.*` group resolves to a
`[Group@N]` section, N = 0..3) and the UE 5.8 Scalability Reference. The
`sg.*` quality scale is **0=Low, 1=Medium, 2=High, 3=Epic** (AntiAliasing goes to
6; ResolutionQuality is a 0–100 percentage, not a 0–3 level).

> Why `sg.*` groups, not raw `r.*`: setting `sg.GlobalIlluminationQuality 0`
> applies the whole `[GlobalIlluminationQuality@0]` block at once
> (`r.Lumen.DiffuseIndirect.Allow=0`, `r.DistanceFieldAO=0`, ...). That is the
> intended throttle surface and it is what the desktop coordinator would poke.

---

## Rung FULL — native, no throttle

```
r.ScreenPercentage 100, sg.ViewDistanceQuality 3, sg.AntiAliasingQuality 3, sg.ShadowQuality 3, sg.GlobalIlluminationQuality 3, sg.ReflectionQuality 3, sg.PostProcessQuality 3, sg.TextureQuality 3, sg.EffectsQuality 3, sg.FoliageQuality 3, sg.ShadingQuality 3
```

Full Lumen GI + Lumen Reflections, full-res, Epic everywhere. This is the
baseline the VRAM sampler measures against — the "what does it cost ungoverned"
number.

`[TextureQuality@3]` sets `r.Streaming.PoolSize=1000`,
`r.Streaming.LimitPoolSizeToVRAM=0`, `r.Streaming.MipBias=0` — i.e. the texture
streamer is allowed to grow and is NOT capped to VRAM.

---

## Rung REDUCED — yield a slice, keep the look

```
r.ScreenPercentage 70, sg.GlobalIlluminationQuality 2, sg.ShadowQuality 2, sg.ReflectionQuality 2, t.MaxFPS 30
```

Lumen still on but at High (`@2`) — GI/Reflections keep their character, shadows
soften slightly, internal render target is 70% linear (~49% of the pixels →
large GPU-time + render-target-memory cut), and the frame cap halves idle GPU
churn. The intended "a heavier job wants the GPU but we stay visibly alive" state.

---

## Rung FLOOR — minimum heartbeat, hand the GPU over

```
r.ScreenPercentage 50, sg.GlobalIlluminationQuality 0, sg.ShadowQuality 0, sg.ReflectionQuality 0, sg.PostProcessQuality 0, r.Streaming.PoolSize 512, r.Streaming.LimitPoolSizeToVRAM 1, t.MaxFPS 5
```

This is the real VRAM-yield rung:

- `sg.GlobalIlluminationQuality 0` → `[GlobalIlluminationQuality@0]` sets
  `r.Lumen.DiffuseIndirect.Allow=0` + `r.DistanceFieldAO=0`. **Disables Lumen
  GI**, freeing the Lumen scene / radiance-cache / surface-cache allocations
  (a genuine VRAM drop, not just GPU time).
- `sg.ReflectionQuality 0` → `[ReflectionQuality@0]` sets
  `r.Lumen.Reflections.Allow=0` (+ `r.SSR.Quality=0`). **Disables Lumen
  Reflections** → frees the reflection-specific Lumen buffers.
- `sg.ShadowQuality 0` collapses shadow-map resolution/cascades → smaller shadow
  atlas (modest VRAM + real GPU time).
- `sg.PostProcessQuality 0` drops bloom/DOF/SSAO render targets (mostly GPU time,
  some RT memory).
- `r.Streaming.PoolSize 512` + `r.Streaming.LimitPoolSizeToVRAM 1` **hard-caps the
  texture streaming pool to 512 MiB and forbids it growing past free VRAM** — the
  single most direct VRAM lever here.
- `r.ScreenPercentage 50` → render at 25% of the pixels: big GPU-time and
  render-target-memory cut.
- `t.MaxFPS 5` → near-static heartbeat; minimal ongoing GPU occupancy so a
  co-resident heavy job (ComfyUI / Blender bake) gets the headroom.

---

## What actually cuts VRAM vs only GPU-time

Load-bearing distinction for the feasibility verdict — verified from the
`BaseScalability.ini` blocks each `sg.*` level expands to:

| Lever | VRAM | GPU-time | Notes |
|---|---|---|---|
| `sg.GlobalIlluminationQuality 0` (Lumen GI off) | **yes** | yes | frees Lumen scene + radiance/surface cache; biggest single Lumen-memory release |
| `sg.ReflectionQuality 0` (Lumen Reflections off) | **yes** | yes | frees reflection Lumen buffers; falls back to SSR-off |
| `r.Streaming.PoolSize 512` | **yes (direct)** | no | hard ceiling on texture pool |
| `r.Streaming.LimitPoolSizeToVRAM 1` | **yes (direct)** | no | streamer won't grow into free VRAM |
| `sg.TextureQuality 0` | **yes** | minor | `@0` = PoolSize 400 + LimitPoolSizeToVRAM 1 + MipBias 16 (smaller mips resident) |
| `r.ScreenPercentage 50/70` | yes (render targets) | **yes (large)** | RT memory scales with pixel count²; dominant GPU-time lever |
| `sg.ShadowQuality 0` | minor (shadow atlas) | **yes** | mostly time |
| `sg.PostProcessQuality 0` | minor (RTs) | **yes** | mostly time |
| `t.MaxFPS 5/30` | no | **yes (occupancy)** | doesn't shrink allocations; reduces how often the GPU is busy → frees *contention*, not *capacity* |

Takeaway for Phase-A: the **memory** that a co-resident heavy job needs back comes
mostly from **disabling Lumen GI + Reflections** and **capping the streaming pool**;
the **contention/time** the desktop yields comes from **ScreenPercentage + MaxFPS**.
`t.MaxFPS` alone yields contention but NOT capacity — important if the bottleneck
is the 4090's 24 GiB, not its clocks.

---

## Sources (verified 2026-06-19)

- This install's `Engine/Config/BaseScalability.ini` — confirmed the
  `[GlobalIlluminationQuality@0..3]`, `[ReflectionQuality@0..3]`,
  `[TextureQuality@0..3]`, `[ShadowQuality@…]`, `[PostProcessQuality@…]` sections
  and their exact expanded `r.*` values quoted above.
- Scalability Reference for Unreal Engine (UE 5.8) —
  dev.epicgames.com/documentation/unreal-engine/scalability-reference-for-unreal-engine
- Lumen Performance Guide (UE 5.8) —
  dev.epicgames.com/documentation/unreal-engine/lumen-performance-guide-for-unreal-engine
  (Low scalability disables Lumen; Medium GI uses Irradiance Final Gather;
  Medium Reflections uses SSR instead of Lumen Reflections.)
- Lumen GI & Reflections (UE 5.8) — `r.DynamicGlobalIlluminationMethod=1` =
  Lumen GI; `r.ReflectionMethod=1`/`=2` for reflection method.
