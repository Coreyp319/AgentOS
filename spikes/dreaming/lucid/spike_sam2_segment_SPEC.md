# ADR-0032 §S — on-box SAM2 spike (run-on-the-4090 checklist)

This is the gate that moves **ADR-0032 from Proposed → Accepted**. It is the one thing the ADR cannot
self-verify: kijai's node + the SAM2 weights are **not on the box** (confirmed 2026-06-21 — `/object_info`
has 1962 classes, none a SAM2 segmenter; no `custom_nodes/ComfyUI-segment-anything-2`, no `models/sam2/`).
Until this runs, every node-signature / coordinate-format / VRAM / license line in ADR-0032 §2/§S is web
research.

## 0. Why a spike and not just trust the research
Three independent reviewers flagged the segmenter as an unverified load-bearing dependency, and the engine
already punishes self-reported model sizes (ADR-0004: they undercount). The spike **self-discovers** the
real node schema from `/object_info` (it does not assume the web-research socket names) and **measures**
peak VRAM with `nvidia-smi`, so the ADR gets ground truth, not a guess.

## 1. Install (you decide — this clones a third-party node + fetches weights; confirm the license)
```
cd ~/ComfyUI/custom_nodes
git clone https://github.com/kijai/ComfyUI-segment-anything-2
pip install -r ComfyUI-segment-anything-2/requirements.txt
mkdir -p ~/ComfyUI/models/sam2
# the loader node can auto-download, OR fetch sam2.1_hiera_small.safetensors into models/sam2/
# >>> CAPTURE the checkpoint LICENSE (expect Apache-2.0) + sha256 of the SMALL variant <<<
systemctl --user restart comfyui     # or however ComfyUI is launched
```

## 2. Discover (safe — read-only schema dump)
```
cd ~/Documents/AgentOS/spikes/dreaming/lucid
./spike_sam2_segment.py discover
```
Confirms the real **segmentation node class**, its **loader** companion, and every **input socket name +
type** (the image input, the model input, the positive-point STRING input, the MASK output slot). If the
node is missing it prints the install block and exits 2.

## 3. Run (build + execute the point→MASK→SaveImage graph; measure VRAM)
```
./spike_sam2_segment.py run                       # generated test frame, center point, 768×1344
./spike_sam2_segment.py run --image some_lucid_frame.png --point 410,720   # a real frame is better
```
It auto-wires the graph from the discovered schema, POSTs it through the app's own `comfy_client`, reads
the mask PNG back via the **image** output path (NOT `generate()`, which is video-only — ADR-0032 §2), and
prints an **`ADR-0032 §S CAPTURE`** block.

## 4. Paste these into ADR-0032 §S (and §2 where noted)
- [ ] segmentation node **class name** + loader class name (→ §2 replaces "`Sam2Segmentation`")
- [ ] **point input socket name** + confirmed JSON shape `[{"x":int,"y":int}]` (→ §2/§3 coordinate contract)
- [ ] image input + model input socket names; MASK output slot index
- [ ] **measured peak VRAM (MiB)** → set `SEG_PEAK_MIB` ≈ that + an ADR-0004 undercount margin; resolve the
      ADR's 0.2 / 1–2 / 2 GB inconsistency to this one number; sanity-check it leaves headroom beside a
      warm ~17–22 GB LTX model on the 24 GB card
- [ ] checkpoint **license** (Apache-2.0?) + **sha256** of the small variant
- [ ] whether a `keep_model_loaded` widget exists; the **negative-point** input name (for the deferred
      +/- refinement)
- [ ] a glance at the returned mask PNG — is a single center tap a clean object silhouette? (sanity for the
      §S premise check that follows)

## 5. After the capture — the two remaining §S items (separate, also owed)
- **Premise check (cheap, offline-ish):** rasterize the SAM mask, a bbox-fitted disc, and the legacy disc;
  downsample each to the **LTX guide latent grid**; report IoU + active-cell delta. Records whether the
  silhouette actually beats a disc at the resolution the model conditions on (ADR-0025's effect was a weak
  1.12×). Feeds the numeric kill line in ADR-0032 Consequences.
- **End-to-end render:** a guided 10Eros beat carrying a segmented mask, proving the mask bites the LTX
  attention and ADR-0025's seed-keyframe invariant (`model.py: total_pre_filter_count == keyframe_grid_mask`)
  still holds — reuse `spike_ltx_attention.py --run`.

Once §4 + §5 are pasted in, ADR-0032 is implementation-ready and the engine seam (already built + offline-
tested: the gate, the schema, the resolve-or-disc branch) wires to the **confirmed** node names.
