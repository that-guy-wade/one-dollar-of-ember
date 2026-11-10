"""Muse Image via OpenRouter /api/v1/images. Exact usage.cost logged to img_ledger.jsonl.
Usage: img.py sprites   |   img.py one <out.webp> <prompt>"""
import base64, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx
from dotenv import load_dotenv
HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
STYLE = ("cozy isometric pixel-art game sprite, warm lantern light, rich colours, single object centred, "
         "isolated on a plain solid very dark blue-black background, no text")
SPRITES = {
    "campfire": "a small campfire camp with three hide tents", "house": "a cosy timber cottage with thatched roof",
    "farm": "a wheat farm field with a little barn", "well": "a stone village well with a wooden crank",
    "granary": "a big wooden granary barn full of sacks of grain", "market": "a busy market square with striped stalls",
    "temple": "a small stone temple with a golden dome", "school": "a red-brick village school with a bell tower",
    "walls": "a section of stone city walls with a gatehouse", "bridge": "an arched stone bridge over a stream",
    "hall": "a grand town hall with flags and a clock", "forge": "a blacksmith forge with glowing anvil and chimney",
    "docks": "wooden fishing docks with a small boat", "tavern": "a two-storey tavern with a hanging sign and warm windows",
    "mine": "a mine entrance in a rocky hill with a cart",
}

def gen(out, prompt):
    t = time.time()
    r = httpx.post("https://openrouter.ai/api/v1/images", timeout=240,
                   headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                   json={"model": "meta/muse-image", "prompt": prompt, "n": 1})
    j = r.json()
    rec = {"out": str(out), "status": r.status_code, "s": round(time.time() - t, 1)}
    if r.status_code == 200:
        Path(out).write_bytes(base64.b64decode(j["data"][0]["b64_json"]))
        rec["cost"] = j.get("usage", {}).get("cost", 0)
    else:
        rec["error"] = str(j)[:200]
    with open(HERE / "img_ledger.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec

if __name__ == "__main__":
    if sys.argv[1] == "sprites":
        jobs = [(HERE / f"static/img/{k}.webp", f"{v}, {STYLE}") for k, v in SPRITES.items()]
        jobs.append((HERE / "static/img/terrain.webp", "top-down fantasy game map terrain: soft green meadows, a winding blue "
                     "river from north to south-east, patches of forest at the edges, dirt paths, no buildings, no text, painterly pixel art, muted night-time palette"))
        with ThreadPoolExecutor(6) as ex:
            for rec in ex.map(lambda a: gen(*a), jobs):
                print(json.dumps(rec), flush=True)
    else:
        print(json.dumps(gen(sys.argv[2], sys.argv[3])))
