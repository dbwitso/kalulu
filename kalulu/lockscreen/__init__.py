"""iOS-style lock screen: Kalulu prepares everything, the GNOME Shell extension draws it.

Kalulu (this package) does the heavy or networked work while you're logged in:
  * finds the wallpaper, makes a sharp copy, and cuts the subject out of it with an on-device
    model so the clock can sit *behind* the subject (the depth effect)
  * picks a clock colour that reads well against the top of the wallpaper
  * fetches the weather
and writes it all to ~/.local/state/kalulu/lockscreen/meta.json.

The extension (data/gnome-shell/kalulu-lockscreen@kalulu.dev) only reads that file, so nothing slow
or fallible ever runs on the lock screen itself.

GTK-free; the GLib/Gio watcher lives in service.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core import paths

log = logging.getLogger("kalulu.lockscreen")

DIR = paths.STATE_DIR / "lockscreen"
META = DIR / "meta.json"
MODEL_DIR = paths.STATE_DIR / "models"
ML_VENV = paths.STATE_DIR / "ml-venv"         # private venv holding onnxruntime (system numpy/OpenCV)
_REMBG = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
# silueta: 44 MB, needs onnxruntime (it's quantised), much cleaner cut-outs.
# u2netp: 4.6 MB fallback that plain OpenCV can run.
MODELS = {
    "silueta": (_REMBG + "silueta.onnx", "75da6c8d2f8096ec743d071951be73b4a8bc7b3e51d9a6625d63644f90ffeedb"),
    "u2netp": (_REMBG + "u2netp.onnx", "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"),
}
META_VERSION = 1
WEATHER_MAX_AGE = 30 * 60

DEFAULT_WIDGETS = ["weather", "battery", "calendar"]


# ---------------------------------------------------------------------- settings
def prefs(cfg: dict) -> dict:
    c = cfg.get("lockscreen", {})
    widgets = [w for w in c.get("widgets", DEFAULT_WIDGETS) if w in ("weather", "battery", "calendar")]
    return {
        "enabled": bool(c.get("enabled", True)),
        "depth": bool(c.get("depth_effect", True)),
        "font": str(c.get("clock_font", "Ubuntu Sans")),
        "weight": int(c.get("clock_weight", 700)),
        "widgets": widgets,
        "event_titles": bool(c.get("show_event_titles", True)),
        "wallpaper": os.path.expanduser(str(c.get("wallpaper", "") or "")),
        "weather_city": str(c.get("weather_city", "") or ""),
        "fahrenheit": bool(c.get("fahrenheit", False)),
    }


# ---------------------------------------------------------------------- wallpaper
def _uri_to_path(uri: str) -> Path | None:
    if not uri:
        return None
    if uri.startswith("file://"):
        uri = urllib.parse.unquote(urllib.parse.urlparse(uri).path)
    p = Path(os.path.expanduser(uri))
    return p if p.exists() else None


def resolve_slideshow(p: Path) -> Path | None:
    """GNOME .xml wallpapers (day/night slideshows): use the first picture in them."""
    try:
        root = ET.parse(p).getroot()
    except (ET.ParseError, OSError):
        return None
    for el in root.iter():
        if el.tag in ("file", "from") and el.text and el.text.strip():
            q = Path(el.text.strip())
            if q.exists():
                return q
        if el.tag == "size" and el.text and Path(el.text.strip()).exists():
            return Path(el.text.strip())
    return None


def current_wallpaper(p: dict, background_settings=None, interface_settings=None) -> Path | None:
    """The lock screen wallpaper: [lockscreen] wallpaper if set, else the desktop one
    (dark variant when the desktop is in dark mode, like GNOME does)."""
    if p.get("wallpaper"):
        w = Path(p["wallpaper"])
        if w.exists():
            return w
        log.warning("lockscreen wallpaper %s not found, using the desktop one", w)
    if background_settings is None:
        return None
    dark = False
    if interface_settings is not None:
        try:
            dark = interface_settings.get_string("color-scheme") == "prefer-dark"
        except Exception:
            dark = False
    uri = background_settings.get_string("picture-uri-dark" if dark else "picture-uri") or \
        background_settings.get_string("picture-uri")
    path = _uri_to_path(uri)
    if path and path.suffix.lower() == ".xml":
        path = resolve_slideshow(path)
    return path


def file_key(p: Path) -> str:
    st = p.stat()
    return hashlib.sha256(f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------------- model
def model_path(name: str) -> Path:
    return MODEL_DIR / f"{name}.onnx"


def _can_import(python: str, mods: str) -> bool:
    try:
        return subprocess.run([python, "-c", f"import {mods}"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def segmenter() -> tuple[str, str] | None:
    """(python, model name) to run the cut-out with, best first; None if OpenCV/numpy are missing."""
    venv_py = ML_VENV / "bin" / "python3"
    if venv_py.exists() and _can_import(str(venv_py), "onnxruntime, numpy, cv2"):
        return str(venv_py), "silueta"
    if _can_import(sys.executable, "onnxruntime, numpy, cv2"):
        return sys.executable, "silueta"
    if _can_import(sys.executable, "numpy, cv2"):
        return sys.executable, "u2netp"
    return None


def ensure_model(name: str = "silueta", progress=None) -> bool:
    """Download a cut-out model once and verify its checksum. Blocking."""
    path = model_path(name)
    if path.exists() and path.stat().st_size > 1_000_000:
        return True
    url, sha = MODELS[name]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    h = hashlib.sha256()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kalulu"})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got / total)
    except (OSError, ValueError) as e:
        log.warning("model download failed: %s", e)
        part.unlink(missing_ok=True)
        return False
    if h.hexdigest() != sha:
        log.warning("model checksum mismatch, discarding download")
        part.unlink(missing_ok=True)
        return False
    part.rename(path)
    return True


def setup_ml(say=print) -> bool:
    """One-off: private venv with onnxruntime + the 44 MB model. Used by install.sh. Blocking."""
    if not _can_import(sys.executable, "numpy, cv2"):
        say("OpenCV/numpy missing: sudo apt install python3-opencv python3-numpy")
        return False
    venv_py = ML_VENV / "bin" / "python3"
    if not (venv_py.exists() and _can_import(str(venv_py), "onnxruntime")):
        say("Setting up the on-device cut-out engine (onnxruntime, one-off)…")
        r = subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(ML_VENV)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            r = subprocess.run([str(venv_py), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                "onnxruntime"], capture_output=True, text=True)
        if r.returncode != 0:
            say("onnxruntime unavailable; using the smaller OpenCV model instead "
                f"({(r.stderr or r.stdout).strip().splitlines()[-1:] or ''})")
    seg = segmenter()
    if not seg:
        return False
    say(f"Downloading the {seg[1]} model (once)…")
    return ensure_model(seg[1])


# ---------------------------------------------------------------------- prepare
def read_meta() -> dict:
    try:
        return json.loads(META.read_text())
    except (OSError, ValueError):
        return {}


def write_meta(meta: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    meta["version"] = META_VERSION
    meta["updated"] = time.time()
    tmp = META.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=1))
    os.replace(tmp, META)                     # the extension never sees a half-written file


def _cleanup(keep: set[str]) -> None:
    for f in DIR.glob("*"):
        if f.name not in keep and f.suffix in (".jpg", ".png"):
            f.unlink(missing_ok=True)


def prepare(p: dict, wallpaper: Path | None, force: bool = False) -> dict:
    """Make the sharp wallpaper copy + subject cut-out for `wallpaper` and update meta.json.
    Blocking (runs the model in a subprocess, a few seconds). Returns the new meta."""
    meta = read_meta()
    meta["prefs"] = {k: p[k] for k in ("font", "weight", "widgets", "event_titles", "depth", "enabled")}
    if not p["enabled"] or wallpaper is None:
        meta.update(wallpaper=None, subject=None, source=None)
        write_meta(meta)
        return meta
    key = file_key(wallpaper) + ("-d" if p["depth"] else "")
    if not force and meta.get("source_key") == key and meta.get("wallpaper") and Path(meta["wallpaper"]).exists():
        write_meta(meta)                       # prefs may have changed
        return meta

    DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    seg = segmenter()
    python = seg[0] if seg else sys.executable
    cmd = [python, "-m", "kalulu.lockscreen.segment", str(wallpaper), str(DIR), key]
    if p["depth"] and seg and ensure_model(seg[1]):
        cmd.append(str(model_path(seg[1])))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        if r.returncode == 0:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        else:
            log.warning("lockscreen prepare failed: %s", (r.stderr or r.stdout).strip()[-500:])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
        log.warning("lockscreen prepare failed: %s", e)

    if not out.get("wallpaper"):
        # Couldn't process it (e.g. an SVG wallpaper, or OpenCV missing): show the original as-is.
        out = {"wallpaper": str(wallpaper), "subject": None, "tint": "#ffffff", "dark_text": False,
               "reason": out.get("reason") or "could not process wallpaper"}
    meta.update(out)
    meta.update(source=str(wallpaper), source_key=key)
    write_meta(meta)
    _cleanup({Path(x).name for x in (meta.get("wallpaper"), meta.get("subject")) if x})
    return meta


# ---------------------------------------------------------------------- weather
_WMO = {
    0: ("Clear", "weather-clear"), 1: ("Mostly clear", "weather-few-clouds"),
    2: ("Partly cloudy", "weather-few-clouds"), 3: ("Overcast", "weather-overcast"),
    45: ("Fog", "weather-fog"), 48: ("Fog", "weather-fog"),
    51: ("Drizzle", "weather-showers-scattered"), 53: ("Drizzle", "weather-showers-scattered"),
    55: ("Drizzle", "weather-showers-scattered"), 56: ("Freezing drizzle", "weather-showers-scattered"),
    57: ("Freezing drizzle", "weather-showers-scattered"),
    61: ("Light rain", "weather-showers-scattered"), 63: ("Rain", "weather-showers"),
    65: ("Heavy rain", "weather-showers"), 66: ("Freezing rain", "weather-showers"),
    67: ("Freezing rain", "weather-showers"), 71: ("Light snow", "weather-snow"), 73: ("Snow", "weather-snow"),
    75: ("Heavy snow", "weather-snow"), 77: ("Snow grains", "weather-snow"),
    80: ("Showers", "weather-showers-scattered"), 81: ("Showers", "weather-showers"),
    82: ("Heavy showers", "weather-showers"), 85: ("Snow showers", "weather-snow"),
    86: ("Snow showers", "weather-snow"), 95: ("Thunderstorm", "weather-storm"),
    96: ("Thunderstorm", "weather-storm"), 99: ("Thunderstorm", "weather-storm"),
}


def describe_weather(code: int, is_day: bool) -> tuple[str, str]:
    label, icon = _WMO.get(int(code), ("Weather", "weather-overcast"))
    if not is_day and icon in ("weather-clear", "weather-few-clouds"):
        icon += "-night"
    return label, icon + "-symbolic"


def gnome_weather_location(shell_weather_settings) -> tuple[str, float, float] | None:
    """The location set in GNOME Weather / the top-bar clock, if any. GWeather stores
    coordinates in radians inside a nested variant; dig out the name and first coordinate pair."""
    if shell_weather_settings is None:
        return None
    try:
        locs = shell_weather_settings.get_value("locations").unpack()
    except Exception:
        return None

    def walk(v, found):
        if isinstance(v, str) and "name" not in found and v.strip():
            found["name"] = v
        elif isinstance(v, (list, tuple)):
            if len(v) == 2 and all(isinstance(x, float) for x in v) and "coords" not in found:
                found["coords"] = v
            for x in v:
                walk(x, found)
        elif hasattr(v, "unpack"):
            walk(v.unpack(), found)

    for loc in locs:
        f: dict = {}
        walk(loc, f)
        if "coords" in f:
            lat, lon = f["coords"]
            return f.get("name", ""), math.degrees(lat), math.degrees(lon)
    return None


def geocode(city: str, http_json) -> tuple[str, float, float] | None:
    q = urllib.parse.quote(city.split(",")[0].strip())
    data = http_json(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&format=json", timeout=6)
    hit = (data or {}).get("results") or []
    if not hit:
        return None
    return hit[0].get("name", city), float(hit[0]["latitude"]), float(hit[0]["longitude"])


def fetch_weather(p: dict, http_json, shell_weather_settings=None) -> dict | None:
    loc = None
    if p.get("weather_city"):
        loc = geocode(p["weather_city"], http_json)
    if loc is None:
        loc = gnome_weather_location(shell_weather_settings)
    if loc is None:
        return None
    name, lat, lon = loc
    unit = "&temperature_unit=fahrenheit" if p.get("fahrenheit") else ""
    data = http_json(
        f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min"
        f"&forecast_days=1&timezone=auto{unit}", timeout=8)
    try:
        cur, daily = data["current"], data["daily"]
        label, icon = describe_weather(cur["weather_code"], bool(cur.get("is_day", 1)))
        return {"city": name, "temp": round(cur["temperature_2m"]), "label": label, "icon": icon,
                "high": round(daily["temperature_2m_max"][0]), "low": round(daily["temperature_2m_min"][0]),
                "time": time.time()}
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def update_weather(p: dict, http_json, shell_weather_settings=None) -> dict | None:
    if "weather" not in p["widgets"] or not p["enabled"]:
        return None
    w = fetch_weather(p, http_json, shell_weather_settings)
    if w:
        meta = read_meta()
        meta["weather"] = w
        write_meta(meta)
    return w

