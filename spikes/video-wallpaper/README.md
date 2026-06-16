# Spike: video-wallpaper — can QtMultimedia Video be the dreaming renderer?

Feasibility probe for **ADR-0008 Surface A** (the QML `Video` wallpaper consumer keyed by
`agent.json`). Question the hills precedent does NOT cover: does a hardware-decoded mp4/webm
**seamlessly loop inside a Plasma 6 wallpaper QML scene**, and can the continuous
`busy/warm/snag` floats post-process that decoding frame the way the aurora shader does?

Throwaway. Box: RTX 4090 / driver 610.43.02, Plasma 6.6.5, Qt 6.11.1 (FFmpeg media backend),
Wayland session.

## Files
- `probe.qml` — bare `qml6` harness: `MediaPlayer{loops:Infinite}` → `VideoOutput` →
  `ShaderEffect(grade.frag.qsb)`. Logs decode path, loop-wrap detection, final state.
- `grade.frag` / `grade.frag.qsb` — the agent.json grade over the video frame
  (snag desaturate+dim, warm low-glow, busy lift). **Idle (all uniforms 0) = passthrough.**

## How to run — ON A REAL SESSION TERMINAL (this is load-bearing)
A bare `qml6` launched from a **detached / non-interactive shell** (e.g. an agent process)
opens the media demuxer but the scene-graph render thread never starts — no `console.log`,
no Timer, exit 0. Same class as `hills-reactive/README.md:43` (offscreen has no GL context).
Run from an actual konsole on the live session:

```bash
cd spikes/video-wallpaper
ffmpeg -f lavfi -i "gradients=size=1024x576:rate=30:duration=6:c0=0x1a2b4a:c1=0x4a6fa5" \
       -c:v libx264 -pix_fmt yuv420p -g 30 /tmp/dream_loop.mp4 -y
QT_LOGGING_RULES="qt.multimedia.ffmpeg*=true" qml6 probe.qml
# watch for: "LOOP WRAP #2" (Infinite re-arms) and the hwaccel line (HW vs software decode)
```

## Findings (measured 2026-06-16)

### Decode IS cheap even with HW decode broken — this is the headline
- **Qt's FFmpeg backend defaults to the VA-API hwaccel path, which FAILS on this NVIDIA
  stack:** `qt.multimedia.ffmpeg.hwaccelvaapi … EGL_BAD_MATCH, disabling hardware
  acceleration`. `libva-nvidia-driver 0.0.17` is installed but its EGL/GBM match is fragile
  headless; direct `h264_cuvid` with a CUDA output surface failed `CUDA_ERROR_INVALID_VALUE`
  in a non-display context. So Qt likely **software-decodes** unless steered with
  `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=cuda` (untested in-scene — needs the session run).
- **But software decode has ~13x realtime headroom:** pure-CPU H.264 decode measured
  **4K 3840×2160 @ ~400 fps** (0.07x realtime) and **1024×576 @ ~3000 fps** on this box.
  A single dream loop costs near-zero CPU even with the GPU decoder unused. **HW decode is a
  nice-to-have, not a gate.** The "GPU under pressure" premise is *helped* by SW decode — it
  keeps the decode off the contended NVDEC/VRAM path entirely.
- **NVDEC session VRAM cost (when it does engage): ~397 MiB** for one 1024×576 stream
  (`nvidia-smi` delta during an `-hwaccel cuda` decode). Bounded and small vs the 1.5GB RT
  eviction lever (ADR-0004).

### The post-process seam HOLDS in principle (idle-byte-identical preserved)
- `VideoOutput` exposes its frame as a sampleable texture to a `ShaderEffect`; the
  `busy/warm/snag` grade is the **same additive math as `aurora.frag`** (snag desaturate+dim,
  warm low-glow), and `grade.frag` is written so **all-uniforms-0 = passthrough** — the
  idle-byte-identical contract survives onto the video path. The continuous floats do NOT
  force discretization: `playbackRate = 1 + busy*k` is continuous, and the grade is continuous.
  **Video does not force "a few discrete loops" for the *grade*** — only the *clip choice*
  (which dream) is discrete; the mood modulation on top stays continuous like the shader.

### Still UNPROVEN (needs the session-terminal run — I could not attach from the agent shell)
- **Seamless loop:** whether `MediaPlayer.Infinite` re-arms with **zero black frame / no
  audible-equivalent hitch** at the wrap. FFmpeg-backend loop is a seek-to-0, historically
  prone to a 1–2 frame gap. `probe.qml` detects wraps; the *visual* seam needs eyes on-session.
- **Per-output / multi-monitor** behavior in a real `WallpaperItem` (one decoder per screen =
  N×397MiB if HW, N× a few % CPU if SW).
- **Whether forcing `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=cuda` makes Qt use NVDEC in-scene**
  (vs the failing vaapi default) — or whether SW decode is simply the shipping answer here.

## Verdict
Surface A's **renderer is feasible** — but the proven, lowest-risk form is
**software-decode the loop + grade it with a ShaderEffect**, NOT a fragile NVIDIA HW-decode
path. The shader is not the *only* renderer; video + the same float grammar composes. The one
genuinely unproven bit is the **loop seam**, which is a 10-minute on-session eyeball, not a
re-architecture.
