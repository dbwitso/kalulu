"""Wallpaper processing for the lock screen. Run as a subprocess so OpenCV and the model's memory are
released straight after:

    python3 -m kalulu.lockscreen.segment WALLPAPER OUTDIR KEY [MODEL.onnx]

Writes OUTDIR/wall-KEY.jpg (sharp copy, max 3840 px) and, when a clear subject is found,
OUTDIR/subject-KEY.png (same size, transparent except the subject). Prints one JSON line:
    {"wallpaper": ..., "subject": ... | null, "subject_top": 0.0-1.0, "tint": "#rrggbb",
     "dark_text": bool, "reason": "..."}

The model is U²-Net "silueta" (salient object detection, 320×320 input). It runs on the CPU through
onnxruntime when that's installed, otherwise through OpenCV's DNN module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

MAX_SIDE = 3840
SIZE = 320
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
CLOCK_BAND = (0.2, 0.8)          # horizontal span where the clock sits


def load_runner(model: str):
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        name = sess.get_inputs()[0].name
        return lambda x: sess.run(None, {name: x})[0]
    except ImportError:
        net = cv2.dnn.readNetFromONNX(model)
        outs = net.getUnconnectedOutLayersNames()

        def run(x):
            net.setInput(x)
            res = net.forward(outs)
            # U²-Net emits d0 (the fused, final map) plus 6 side outputs; d0 comes first in the graph
            # but OpenCV doesn't promise output order, so take the one that agrees most with the rest.
            maps = [r.reshape(SIZE, SIZE) for r in res if r.size == SIZE * SIZE]
            if len(maps) == 1:
                return maps[0][None, None]
            mean = np.mean(maps, axis=0)
            best = min(maps, key=lambda m: float(np.abs(m - mean).mean()))
            return best[None, None]
        return run


def subject_mask(img_bgr: np.ndarray, run) -> np.ndarray:
    """Float mask in 0..1 at the image's size."""
    h, w = img_bgr.shape[:2]
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    small = cv2.resize(rgb, (SIZE, SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    small /= max(float(small.max()), 1e-6)
    x = ((small - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    pred = np.asarray(run(x))[:, 0, :, :][0]
    lo, hi = float(pred.min()), float(pred.max())
    pred = (pred - lo) / max(hi - lo, 1e-6)
    return cv2.resize(pred, (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1)


def refine(mask: np.ndarray) -> np.ndarray:
    """Crisp but anti-aliased edges: steepen the soft map, then feather by ~1.5 px."""
    a = ((mask - 0.4) / 0.25).clip(0, 1)
    k = max(3, int(round(min(mask.shape) / 700)) * 2 + 1)
    return cv2.GaussianBlur(a, (k, k), 0)


def keep_main(alpha: np.ndarray) -> np.ndarray:
    """Drop specks: keep connected regions at least a fifth the size of the biggest one."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 0.5).astype(np.uint8), connectivity=8)
    if n <= 2:
        return alpha
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.zeros(n, dtype=bool)
    keep[1:] = areas >= areas.max() * 0.2
    region = keep[labels].astype(np.uint8)
    region = cv2.dilate(region, np.ones((5, 5), np.uint8))      # keep the feathered edge around kept parts
    return alpha * region


def profile(alpha: np.ndarray, bins: int = 96) -> list[float]:
    """The subject's outline: for each of `bins` vertical strips, where the subject starts (fraction of
    image height; 1.0 = not in that strip). Lets the lock screen size the clock so the subject only
    covers a readable share of the digits."""
    h, w = alpha.shape
    solid = alpha > 0.5
    out = []
    for i in range(bins):
        strip = solid[:, int(i * w / bins):max(int((i + 1) * w / bins), int(i * w / bins) + 1)]
        rows = np.where(strip.mean(axis=1) > 0.3)[0]
        out.append(round(float(rows[0]) / h, 4) if rows.size else 1.0)
    return out


def analyse(alpha: np.ndarray) -> tuple[float, float, str]:
    """(subject_top, area, reason). subject_top is where the subject starts inside the clock band."""
    h, w = alpha.shape
    area = float((alpha > 0.5).mean())
    band = alpha[:, int(w * CLOCK_BAND[0]):int(w * CLOCK_BAND[1])] > 0.5
    rows = np.where(band.mean(axis=1) > 0.04)[0]
    top = float(rows[0]) / h if rows.size else 1.0
    if area < 0.015:
        return top, area, "no clear subject"
    if area > 0.6:
        return top, area, "subject fills the picture"
    if rows.size == 0:
        return top, area, "subject is not under the clock"
    if top < 0.16:
        return top, area, "subject reaches the top edge; the clock would be hidden"
    return top, area, ""


def tint_for(img_bgr: np.ndarray, top: float) -> tuple[str, bool]:
    """Clock colour: a light tint of the sky/area behind the clock, or dark text on very bright tops."""
    h, w = img_bgr.shape[:2]
    region = img_bgr[: max(1, int(h * min(max(top, 0.25), 0.45))), int(w * 0.2):int(w * 0.8)]
    b, g, r = (float(c) / 255 for c in region.reshape(-1, 3).mean(axis=0))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if lum > 0.72:
        mix = [c * 0.35 for c in (r, g, b)]                    # deep tone of the same hue
        return "#%02x%02x%02x" % tuple(int(c * 255) for c in mix), True
    mix = [0.82 + 0.18 * c for c in (r, g, b)]                 # mostly white, a hint of the scene
    return "#%02x%02x%02x" % tuple(min(255, int(c * 255)) for c in mix), False


def main(argv: list[str]) -> int:
    src, out, key = Path(argv[1]), Path(argv[2]), argv[3]
    model = argv[4] if len(argv) > 4 else None
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        print(json.dumps({"reason": f"can't read {src.suffix or 'this'} wallpaper"}))
        return 0
    h, w = img.shape[:2]
    s = min(1.0, MAX_SIDE / max(h, w))
    if s < 1.0:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    out.mkdir(parents=True, exist_ok=True)
    wall = out / f"wall-{key}.jpg"
    cv2.imwrite(str(wall), img, [cv2.IMWRITE_JPEG_QUALITY, 93])

    result = {"wallpaper": str(wall), "subject": None, "subject_top": 1.0, "reason": "depth effect off",
              "width": img.shape[1], "height": img.shape[0]}
    if model:
        alpha = keep_main(refine(subject_mask(img, load_runner(model))))
        top, area, reason = analyse(alpha)
        result.update(subject_top=round(top, 4), subject_area=round(area, 4), reason=reason)
        if not reason:
            result["profile"] = profile(alpha)
            rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            rgba[:, :, 3] = (alpha * 255).astype(np.uint8)
            rgba[alpha < 0.004] = 0                     # fully clear pixels compress to nothing
            subj = out / f"subject-{key}.png"
            cv2.imwrite(str(subj), rgba, [cv2.IMWRITE_PNG_COMPRESSION, 6])
            result["subject"] = str(subj)
    tint, dark = tint_for(img, result["subject_top"])
    result.update(tint=tint, dark_text=dark)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
