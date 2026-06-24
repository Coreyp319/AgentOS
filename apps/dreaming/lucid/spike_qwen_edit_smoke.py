#!/usr/bin/env python3
"""ADR-0040 smoke + VRAM measurement (throwaway spike, like measure_glimpse_vram.py).

Build the Qwen-Image-Edit graph (lucid_engine._edit_graph), submit it to a RUNNING ComfyUI, confirm a
visibly-edited PNG comes back, and report peak GPU memory so EDIT_PEAK_MIB can be set HONESTLY (the repo
measures, it doesn't guess). Needs ComfyUI up on :8188 (start-comfyui.sh) and a frame in ComfyUI/input.

    python spike_qwen_edit_smoke.py [frame_name_in_input] ["edit instruction"]
"""
import glob
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # apps/dreaming (comfy_client)
sys.path.insert(0, HERE)
import comfy_client as cc   # noqa: E402
import lucid_engine as E    # noqa: E402


def gpu_used_mib():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True)
        return int(out.strip().splitlines()[0])
    except Exception:
        return -1


def main():
    frame = sys.argv[1] if len(sys.argv) > 1 else None
    if not frame:
        ins = sorted(glob.glob(os.path.join(E.INPUT_DIR, "*.png")), key=os.path.getmtime, reverse=True)
        if not ins:
            print("no input png found — pass a frame name"); return 2
        frame = os.path.basename(ins[0])
    instruction = sys.argv[2] if len(sys.argv) > 2 else \
        "the person raises one hand toward the light and turns their head slightly toward it"
    print(f"frame={frame!r}\ninstruction={instruction!r}")
    print(f"lightning={bool(E.EDIT_LIGHTNING_LORA)}  model={E.EDIT_MODEL}  te={E.EDIT_TE}")

    base = gpu_used_mib()
    peak = [base]
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            peak[0] = max(peak[0], gpu_used_mib())
            time.sleep(0.25)
    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    g = E._edit_graph(frame, instruction, seed=42)
    t0 = time.time()
    try:
        imgs, _hist = cc.generate_image(g, timeout=600)
    except Exception as e:
        stop.set(); th.join()
        print(f"\nSMOKE FAIL — submit raised: {type(e).__name__}: {e}")
        return 1
    dt = time.time() - t0
    stop.set(); th.join()

    print(f"\nRESULT imgs={imgs}")
    print(f"elapsed={dt:.1f}s  base_used={base}MiB  peak_used={peak[0]}MiB  delta_over_base={peak[0]-base}MiB")
    if imgs:
        try:
            from PIL import Image
            with Image.open(imgs[0]) as im:
                print(f"output size={im.size}")
        except Exception as e:
            print(f"(could not open output: {e})")
        print("SMOKE OK")
        return 0
    print("SMOKE FAIL — no image returned")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
