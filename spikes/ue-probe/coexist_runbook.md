# Coexistence runbook — throttled UE + ComfyUI + Ollama on one RTX 4090

Phase-A feasibility. **YOU run every load, sequentially**, sampling VRAM between each so
the measurement isn't perturbed. Goal: see whether a throttled UE wallpaper + a small
ComfyUI-resident image model + a small Ollama-resident model coexist under 24 GiB without
OOM/crash. Gentle (small models) first; the heavy Wan-14B case is a known cliff, noted at
the end.

Card: RTX 4090, 24 GiB (≈ 23.5 GiB usable per ComfyUI `vram_total`).
ComfyUI is up at `127.0.0.1:8188` with `--disable-smart-memory` (loaded checkpoint stays
resident until `/free`). Ollama is up at `127.0.0.1:11434`, currently 0 models resident.

## Sampling helpers (read-only — use these between every step)
```bash
# (i) ground-truth whole-GPU VRAM (all tenants, incl. desktop):
nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv,noheader

# (ii) per-process VRAM (which PID holds what):
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader

# (iii) ComfyUI's own view (used/free for cuda:0):
curl -s 127.0.0.1:8188/system_stats | python3 -c \
 'import sys,json;d=json.load(sys.stdin)["devices"][0];print("comfy used",(d["vram_total"]-d["vram_free"])//2**20,"MiB free",d["vram_free"]//2**20,"MiB")'

# (iv) Ollama residency + size_vram:
curl -s 127.0.0.1:11434/api/ps | python3 -m json.tool
```
Record `nvidia-smi` (i) at every step — it is the authoritative total. The per-tenant
views (iii/iv) attribute the delta.

---

## Step (a) — confirm throttled UE resident + baseline sample
1. Confirm the throttled UE editor/wallpaper process is up and on the GPU:
   ```bash
   nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
   ```
   You should see the UE process holding ~4.5 GiB (the `vram_*.log` in this dir shows
   ~4,450 MiB used while throttled).
2. **Sample baseline** with helper (i). Note `UE_BASE = memory.used` MiB. (Expect roughly
   4,400-4,700 MiB total incl. desktop.)
3. Tail the UE log so you can watch for errors later:
   ```bash
   tail -n 3 spikes/ue-probe/ue_editor_*.log
   ```

## Step (b) — load the small ComfyUI checkpoint, sample
1. Run the gentle ComfyUI probe (loads `sd_turbo` ~4.86 GiB disk, 512x512 / 1 step,
   leaves it resident):
   ```bash
   python3 spikes/ue-probe/comfy_load_small.py
   ```
   It prints `[vram:before]` / `[vram:after]` from ComfyUI and announces residency.
2. **Sample** with (i) and (iv). Note `AFTER_COMFY = memory.used`.
   - ComfyUI delta = `AFTER_COMFY - UE_BASE` (expect ~3,000-4,500 MiB for sd_turbo+latents).
3. Sanity: ComfyUI still responsive — `curl -s 127.0.0.1:8188/system_stats >/dev/null && echo ok`.

## Step (c) — load the small Ollama model, sample
1. Run the gentle Ollama probe (loads `moondream:latest` ~1.7 GB, keep_alive 10m):
   ```bash
   bash spikes/ue-probe/ollama_load_small.sh
   ```
   It prints `/api/ps` with `size_vram` confirming residency.
2. **Sample** with (i) and (iv). Note `AFTER_OLLAMA = memory.used`.
   - Ollama delta = `AFTER_OLLAMA - AFTER_COMFY` (expect ~2,000-2,500 MiB; confirm against
     `size_vram` from `/api/ps`).

## Step (d) — check for OOM / crash (all three resident now)
Run all of these; none should show failure:
```bash
# kernel OOM-killer / Xid GPU faults:
dmesg | tail -n 40 | grep -iE 'oom|killed process|nvrm|xid|out of memory' || echo "dmesg: clean"

# UE log tail — look for GPU/D3D/Vulkan device-lost / alloc-fail:
tail -n 30 spikes/ue-probe/ue_editor_*.log | grep -iE 'error|fail|out of memory|device lost|oom' || echo "UE log: clean"

# ComfyUI still answers (process didn't die from a CUDA OOM):
curl -s 127.0.0.1:8188/system_stats >/dev/null && echo "ComfyUI: alive" || echo "ComfyUI: DOWN"

# Ollama still answers:
curl -s 127.0.0.1:11434/api/ps >/dev/null && echo "Ollama: alive" || echo "Ollama: DOWN"
```
If `nvidia-smi` ever errors or a process vanished from `--query-compute-apps`, that
tenant was likely OOM-killed — record which one and at what total.

## Step (e) — totals + headroom verdict
1. Final authoritative sample (i): `TOTAL_USED = memory.used`, `TOTAL_FREE = memory.free`.
2. Attribute it:
   ```
   UE        ≈ UE_BASE                         (~4.5 GiB)
   ComfyUI   ≈ AFTER_COMFY  - UE_BASE          (~3-4.5 GiB)
   Ollama    ≈ AFTER_OLLAMA - AFTER_COMFY      (~2-2.5 GiB)
   desktop   ≈ remainder (Plasma/compositor)   (~1-2 GiB)
   ```
3. **Verdict for the gentle case:** all three fit under 23.5 GiB iff
   `TOTAL_USED < ~22 GiB` (leaving ~1.5 GiB compositor/spike headroom) AND `TOTAL_FREE`
   stayed > ~1 GiB throughout AND step (d) was clean. Expected sum ~10-11.5 GiB used →
   **~12 GiB free → PASS, no OOM.**
4. Record the numbers in `coexist_inventory.md` (or a results note) for the survey.

---

## Heavy-case note (Wan 2.2 14B AIO ~17 GiB) — measure SECOND, expect a cliff
The same sequence but step (b) loads the heavy video checkpoint instead:
```bash
# heavy ComfyUI load (only after the gentle pass is recorded):
python3 ../dreaming/test_aio.py --prompt "test" --length 17 --steps 4 \
  --ckpt wan2.2-rapid-mega-aio-nsfw-v12.2.safetensors --w 480 --h 480
```
Budget math: throttled UE ~4.5 + Wan 14B ~17 + Ollama-small ~2.5 + desktop ~1.5
= **~25.5 GiB > 23.5 GiB usable** → **expected OOM / CUDA alloc failure or eviction.**

What to watch / expect:
- Most likely: **ComfyUI CUDA OOM** on the Wan load (it needs the most contiguous VRAM),
  OR Ollama spills to CPU (`size_vram < size` in `/api/ps`), OR the UE device is lost.
- **The intended resolution is NOT "make all three fit"** — it is the substrate's job:
  the throttled UE must **fall back to the shader wallpaper** (yield the GPU, ADR-0004
  graphics-yield), and/or ComfyUI must be **VRAM-leased** so only one heavy tenant is
  resident at a time (ADR-0006/0010 coordinator + ADR-0018 warm-pool / heavy-lane).
- So the heavy case is the evidence that coexistence needs arbitration, not just headroom.
  Record exactly *which* tenant fails first and at what total — that informs the lease
  priority ordering.

### Cleanup after the runs (release VRAM)
```bash
python3 ../dreaming/comfy_client.py free                                  # release ComfyUI checkpoint
curl -s 127.0.0.1:11434/api/generate -d '{"model":"moondream:latest","keep_alive":0}'  # evict Ollama
```
(UE is yours to stop/keep as the measurement needs.)
