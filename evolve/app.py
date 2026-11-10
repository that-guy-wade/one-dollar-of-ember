"""Ember Evolve: AlphaEvolve-style program search on a $1 Muse Ember budget.

Ember mutates small Python programs that pack 26 circles in a unit square;
a free, sandboxed local grader scores them (sum of radii, overlaps shrunk away).
"""
import argparse, asyncio, json, math, os, random, re, sys, tempfile, time
from pathlib import Path

import httpx, uvicorn
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).parent
N = 26
MODEL = "meta/muse-ember"
URL = "https://openrouter.ai/api/v1/chat/completions"
ALPHAEVOLVE = 2.635  # AlphaEvolve (2025) reported sum of radii for n=26
HINTS = [
    "start from a hexagonal / staggered-row lattice and tune row counts",
    "optimize all centers and radii jointly with scipy.optimize.minimize (SLSQP) with pairwise non-overlap and wall constraints",
    "use a penalty method: maximize sum(r) minus big penalty for overlaps, with L-BFGS-B, then shrink to feasibility",
    "put bigger circles in the middle and small ones in the corners and edge gaps",
    "run several random restarts of the optimizer and keep the best feasible result",
    "compute each radius exactly as the largest feasible value given centers (linear program via scipy.optimize.linprog)",
    "perturb the parent's centers slightly and re-optimize radii; keep improvements",
    "use different row patterns such as 5-5-6-5-5 or 6-5-6-5-4 and pick the best",
    "vectorize constraint functions and supply analytic Jacobians so the optimizer converges in time",
    "simplify: keep what works, remove dead code, tighten the optimizer tolerance",
    "fill leftover gaps with small circles after placing a near-optimal arrangement of large ones",
    "symmetry: build a mirror-symmetric layout and optimize the free half",
]
SYSTEM = "You improve Python programs. Reply with exactly one ```python code block and nothing else."
TASK = (f"Task: place {N} circles inside the unit square [0,1]^2 to MAXIMIZE the sum of radii. "
        f"Write `def pack():` returning (centers, radii): {N} (x,y) and {N} r. numpy and scipy allowed. "
        "Must finish in under 3 s on 1 CPU. Strictly verified (tol 1e-9): any overlap or circle leaving the square scores 0, so end by shrinking radii until exactly feasible.")


TOL = 1e-9


def verify(centers, radii):
    """Strict grader: exactly N circles, every radius > 0, each fully inside [0,1]^2, no pair overlapping (tol 1e-9)."""
    c = [(float(x), float(y)) for x, y in centers]
    r = [float(v) for v in radii]
    if len(c) != N or len(r) != N:
        return {"error": f"need exactly {N} circles, got {len(c)}/{len(r)}"}
    if not all(math.isfinite(v) for v in r + [w for p in c for w in p]):
        return {"error": "non-finite value"}
    if min(r) <= 0:
        return {"error": "non-positive radius"}
    wall = min(min(x - ri, 1 - x - ri, y - ri, 1 - y - ri) for (x, y), ri in zip(c, r))
    pair = min(math.dist(c[i], c[j]) - r[i] - r[j] for i in range(N) for j in range(i + 1, N))
    if wall < -TOL or pair < -TOL:
        return {"error": f"infeasible: wall slack {wall:.1e}, pair slack {pair:.1e}"}
    return {"centers": c, "radii": r, "score": math.fsum(r), "shrunk": 0.0}


def sandbox_env():  # no API key, single-threaded math libraries
    return {"PATH": os.environ.get("PATH", "/usr/bin"), "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1", "HOME": tempfile.gettempdir()}


async def evaluate(code, sem):
    async with sem:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cand.py"; p.write_text(code)
            t0 = time.time()
            proc = await asyncio.create_subprocess_exec(sys.executable, "-I", str(ROOT / "runner.py"), str(p), cwd=d,
                                                        env=sandbox_env(), stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill(); await proc.wait()
                return {"error": "timeout (>5 s)", "secs": 5.0}
            secs = round(time.time() - t0, 2)
            m = re.search(rb"@@RESULT@@(.*)", out)
            if not m:
                return {"error": (err.decode(errors="replace").strip().splitlines() or ["no result"])[-1][:200], "secs": secs}
            try:
                res = json.loads(m.group(1)); g = verify(res["centers"], res["radii"])
            except Exception as e:
                return {"error": f"bad output: {e}"[:200], "secs": secs}
            return g | {"secs": secs}


class Evolver:
    def __init__(self, name, budget, conc, key):
        self.name, self.budget, self.conc, self.key = name, budget, conc, key
        self.path = ROOT / "runs" / f"{name}.json"
        self.s = {"run": name, "budget": budget, "model": MODEL, "alphaevolve": ALPHAEVOLVE, "status": "starting",
                  "started": time.time(), "elapsed": 0, "spent": 0.0, "calls": 0, "ok": 0, "failed_calls": 0,
                  "tok_in": 0, "tok_out": 0, "tok_reason": 0, "programs": [], "history": [], "best": None, "seed": None}
        self.inflight = 0
        self.llm = asyncio.Semaphore(conc)  # <=conc concurrent Ember calls
        self.lock = asyncio.Lock()

    def save(self):
        self.s["elapsed"] = round(time.time() - self.s["started"], 1)
        tmp = self.path.with_suffix(".tmp"); tmp.write_text(json.dumps(self.s)); tmp.replace(self.path)

    def top(self, k=8):
        ok = [p for p in self.s["programs"] if p.get("score") is not None]
        return sorted(ok, key=lambda p: -p["score"])[:k]

    def add(self, prog):
        self.s["programs"].append(prog)
        if prog.get("score") is not None and (self.s["best"] is None or prog["score"] > self.s["best"]["score"] + 1e-9):
            self.s["best"] = prog
        self.s["history"].append({"spent": round(self.s["spent"], 6), "score": prog.get("score"),
                                  "best": self.s["best"]["score"] if self.s["best"] else 0, "id": prog["id"]})

    async def call(self, client, msgs):
        body = {"model": MODEL, "messages": msgs, "reasoning": {"effort": "minimal"}, "usage": {"include": True},
                "max_tokens": 6000, "temperature": 0.9}
        for attempt in range(5):
            try:
                async with self.llm:
                    r = await client.post(URL, json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=120)
                if r.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(2 ** attempt + random.random()); continue
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError):
                await asyncio.sleep(2 ** attempt + random.random())
        return None

    def est(self):
        n = max(1, self.s["calls"])
        return max(0.002, 1.5 * self.s["spent"] / n) if self.s["calls"] else 0.003

    async def worker(self, client, sem):
        while True:
            async with self.lock:
                if self.s["spent"] + (self.inflight + 1) * self.est() > self.budget:
                    return
                self.inflight += 1
                pool = self.top()
                parent = pool[0] if random.random() < 0.5 else random.choice(pool)
                hint = random.choice(HINTS)
                best = self.s["best"]["score"]
            user = (f"{TASK}\nParent program (score {parent['score']:.4f}; best so far {best:.4f}).\n"
                    f"```python\n{parent['code']}\n```\nIdea to try: {hint}\nReturn the full improved program, compact (under 70 lines), no comments.")
            t0 = time.time()
            resp = await self.call(client, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
            llm_secs = round(time.time() - t0, 1)
            if resp is None or not resp.get("choices"):
                async with self.lock:
                    self.inflight -= 1; self.s["failed_calls"] += 1
                await asyncio.sleep(3); continue
            u = resp.get("usage") or {}
            text = resp["choices"][0]["message"].get("content") or ""
            m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
            code = (m.group(1) if m else text).strip()
            res = await evaluate(code, sem) if "def pack" in code else {"error": "no pack() in reply", "secs": 0}
            async with self.lock:
                self.inflight -= 1
                self.s["calls"] += 1
                self.s["spent"] += float(u.get("cost") or 0)
                self.s["tok_in"] += int(u.get("prompt_tokens") or 0)
                self.s["tok_out"] += int(u.get("completion_tokens") or 0)
                self.s["tok_reason"] += int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
                prog = {"id": len(self.s["programs"]), "parent": parent["id"], "hint": hint, "code": code,
                        "cost": float(u.get("cost") or 0), "tok_in": u.get("prompt_tokens"), "tok_out": u.get("completion_tokens"),
                        "llm_secs": llm_secs, "eval_secs": res.get("secs"), "t": round(time.time() - self.s["started"], 1)}
                if "error" in res:
                    prog |= {"score": None, "error": res["error"]}
                else:
                    self.s["ok"] += 1
                    prog |= {k: res[k] for k in ("score", "shrunk", "centers", "radii")}
                self.add(prog)
                self.save()

    async def run(self):
        sem = asyncio.Semaphore(4)  # <=4 local cores for grading
        seed_code = (ROOT / "seed.py").read_text().strip()
        res = await evaluate(seed_code, sem)
        seed = {"id": 0, "parent": None, "hint": "hand-written seed", "code": seed_code, "cost": 0, "t": 0} | \
               {k: res[k] for k in ("score", "shrunk", "centers", "radii")}
        self.s["seed"] = seed; self.add(seed); self.s["status"] = "running"; self.save()
        async with httpx.AsyncClient(http2=False, limits=httpx.Limits(max_connections=self.conc)) as client:
            await asyncio.gather(*(self.worker(client, sem) for _ in range(self.conc + 6)))
        self.s["status"] = "done"; self.save()


def make_app(args):
    app = FastAPI()
    state = {"ev": None}

    @app.on_event("startup")
    async def _start():  # noqa: lifespan is fine for a demo
        if args.replay:
            return
        key = dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY")
        ev = Evolver(args.run, args.budget, args.conc, key)
        state["ev"] = ev
        asyncio.create_task(ev.run())

    @app.get("/api/state")
    def api_state():
        if state["ev"]:
            s = dict(state["ev"].s); s["elapsed"] = round(time.time() - s["started"], 1)
        else:
            s = json.loads((ROOT / "runs" / f"{args.run}.json").read_text())
        light = [{k: p.get(k) for k in ("id", "parent", "score", "cost", "hint", "error", "t", "tok_out")} for p in s["programs"]]
        lineage, i = [], s["best"]["id"] if s["best"] else None
        while i is not None:
            lineage.append({"id": i, "score": light[i]["score"], "hint": light[i]["hint"]}); i = light[i]["parent"]
        return JSONResponse(s | {"programs": light[-400:], "n_programs": len(light), "lineage": lineage[::-1],
                                 "top": [{k: p.get(k) for k in ("id", "score", "hint", "parent")}
                                         for p in sorted([p for p in s["programs"] if p.get("score") is not None],
                                                         key=lambda p: -p["score"])[:8]]})

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="dev")
    ap.add_argument("--budget", type=float, default=0.05)
    ap.add_argument("--conc", type=int, default=8)
    ap.add_argument("--port", type=int, default=8101)
    ap.add_argument("--replay", action="store_true", help="serve a finished run from runs/<run>.json without spending")
    a = ap.parse_args()
    uvicorn.run(make_app(a), host="0.0.0.0", port=a.port, log_level="warning")
