"""Serves the replay (run.json) and a live world you can tick and disrupt. Run: .venv/bin/python server.py"""

import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import sim

HERE = Path(__file__).resolve().parent
LIVE_CAP_USD = 0.10  # hard cap on live-mode spend per server process
app = FastAPI()
live = {"w": None, "lock": asyncio.Lock()}


def snapshot(w, frame):
    return {"frame": frame, "mem": [a["mem"] for a in w.agents], "allies": [a["allies"] for a in w.agents],
            "know": [a["know"] for a in w.agents]}


@app.get("/api/live/state")
async def live_state():
    w = live["w"] = sim.World(1000)
    f = {"d": 0, "s": 0, "news": ["Live world: same 1,000 townsfolk. Tick or inject a crisis."],
         "pos": [[a["x"], a["y"], a["mood"], a["food"]] for a in w.agents], "ev": [], "nev": 0, "active": 0,
         "routine": 0, "rum": {}, "gran": w.granary,
         "state": {"fire": False, "election": None, "tally": {}, "festival": False, "shortage": False},
         "ledger": dict(w.ledger)}
    return {"meta": {"places": {k: v for k, v in sim.PLACES.items() if k != "home"}}, "frames": [f],
            "ledger": w.ledger, "agents": [{k: a[k] for k in ("id", "n", "j", "h")} | {"mem": [], "allies": [], "know": []}
                                           for a in w.agents]}


@app.post("/api/live/tick")
async def live_tick(inject: str = "tick"):
    w = live["w"] or sim.World(1000)
    live["w"] = w
    if w.ledger["cost"] >= LIVE_CAP_USD:
        return JSONResponse({"error": f"live budget cap ${LIVE_CAP_USD} reached"})
    inj = []
    if inject == "rumour":
        inj = [("rumour", w.rng.choice(["the mayor hoards grain", "wolves stalk the farms", "the smith forges for bandits"]))]
    elif inject in ("fire", "shortage", "election", "festival"):
        inj = [(inject, "gran" if inject == "fire" else None)]
    async with live["lock"]:
        async with sim.client() as c:
            frame = await w.step(c, asyncio.Semaphore(8), rate=0.04, batch=40, cap=80, injected=inj)
    return snapshot(w, frame)


@app.get("/run.json")
async def run_json():
    return FileResponse(HERE / "run.json")


@app.get("/")
async def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8102, log_level="warning")
