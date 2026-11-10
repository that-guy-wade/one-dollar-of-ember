"""A $1 civilization: ~1,000 Muse Ember townspeople, batched decisions, deterministic world.

Usage: .venv/bin/python sim.py --agents 1000 --days 7 --budget 0.95 --out run.json
"""

import argparse
import asyncio
import json
import math
import os
import random
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "meta/muse-ember"

JOBS = ["farmer", "baker", "miner", "smith", "merchant", "guard", "healer", "priest", "bard", "fisher"]
HAMLETS = {"N": (50, 16), "S": (50, 84), "W": (16, 50), "E": (84, 50)}
PLACES = {
    "mkt": ("market", 50, 50), "hall": ("town hall", 56, 42), "tav": ("tavern", 43, 56),
    "farm": ("farms", 22, 22), "gran": ("granary", 30, 30), "mine": ("mine", 82, 18),
    "forge": ("forge", 72, 30), "dock": ("docks", 84, 82), "temp": ("temple", 20, 78),
    "well": ("well", 50, 62), "home": ("home", 0, 0),
}
WORKPLACE = {"farmer": "farm", "baker": "gran", "miner": "mine", "smith": "forge", "merchant": "mkt",
             "guard": "hall", "healer": "temp", "priest": "temp", "bard": "tav", "fisher": "dock"}
SCALE = 2.5
BUILD = {  # type: (labour, era needed, map anchor)
    "house": (100, 0, None), "farm": (150, 0, (24, 24)), "well": (100, 0, (50, 60)),
    "granary": (250, 1, (32, 32)), "market": (250, 1, (52, 48)), "tavern": (200, 1, (42, 57)),
    "forge": (200, 1, (72, 32)), "docks": (200, 1, (84, 80)), "mine": (200, 1, (82, 18)),
    "temple": (400, 2, (20, 76)), "school": (400, 2, (62, 64)), "bridge": (400, 2, (66, 76)),
    "hall": (500, 2, (57, 40)), "walls": (600, 3, None),
}
ERAS = ["Camp", "Village", "Town", "City"]
UNLOCK = {"smith": "forge", "miner": "mine", "fisher": "docks", "priest": "temple", "healer": "temple",
          "merchant": "market", "bard": "tavern", "baker": "granary", "guard": "hall"}
ACTIONS = ["propose", "build", "work", "eat", "trade", "gossip", "help", "ally", "rest", "pray", "protest", "vote", "firefight", "flee", "steal", "feast"]
SLOTS = ["dawn", "noon", "dusk"]
SYL = ["ba", "ro", "ka", "li", "mo", "te", "na", "vi", "su", "da", "el", "or", "an", "is", "ul", "fe", "ga", "ri", "to", "we"]

SYSTEM = (
    "You simulate townsfolk of Emberfall, a medieval town of 1000. Each line is one person: "
    "id name job hamlet food-stock coin hunger(0-9, high=starving) mood(-3..3) near=[ids nearby] know=[rumours] notes. "
    "Decide each person's next action in character, reacting to news, needs, neighbours and notes. "
    "BUILDING the town is everyone's shared project: propose(t=building type from 'can propose', s=a short proud name for it), "
    "vote(t=project id P#, to approve a proposed project; or candidate id in an election), build(t=project id under construction, give labour+coin). "
    "Other actions: work(earn/produce at job; jobs marked ? are locked until their building exists), eat(food), trade(t=person id, swap goods), gossip(t=person, pass a rumour), "
    "help(t=person, give food), ally(t=person, form bond), rest, pray, protest(at hall), vote(t=candidate id, only during election), "
    "firefight(only during fire), flee(go home), steal(t=person), feast(only during festival). "
    "t is a person id from near/notes or a candidate id, else \"\". s = what they say or think (or the project name if propose), max 8 words, vivid, specific. "
    "f = mood change -1, 0 or 1. Return one entry per person, same order, ids exact."
)
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["d"],
    "properties": {"d": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["i", "a", "t", "s", "f"],
        "properties": {"i": {"type": "string"}, "a": {"type": "string", "enum": ACTIONS},
                       "t": {"type": "string"}, "s": {"type": "string"}, "f": {"type": "integer", "enum": [-1, 0, 1]}}}}},
}

# Scripted crises for the $1 run (day index -> list of (slot, event))
SCRIPT = {
    1: [(0, ("rumour", "the well is poisoned"))],
    3: [(1, ("fire", None))],
    5: [(0, ("shortage", None))],
    7: [(0, ("election", None))],
    9: [(0, ("rumour", "the mayor hoards grain"))],
    10: [(1, ("fire", "granary"))],
    12: [(1, ("festival", None))],
}


def b36(n):
    s = ""
    while True:
        n, r = divmod(n, 36)
        s = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + s
        if n == 0:
            return s


class World:
    def __init__(self, n, seed=7):
        self.rng = random.Random(seed)
        r = self.rng
        self.agents = []
        for k in range(n):
            h = r.choice(list(HAMLETS))
            hx, hy = HAMLETS[h]
            name = (r.choice(SYL) + r.choice(SYL) + (r.choice(SYL) if r.random() < 0.4 else "")).capitalize()
            job = r.choices(JOBS, weights=[22, 8, 10, 7, 10, 8, 5, 4, 5, 10])[0]
            hx2, hy2 = hx + r.gauss(0, 6), hy + r.gauss(0, 6)
            cx_, cy_ = 50 + r.gauss(0, 7), 50 + r.gauss(0, 7)
            self.agents.append({
                "id": b36(k + 36), "n": name, "j": job, "h": h, "hx": round(hx2, 1), "hy": round(hy2, 1),
                "x": round(cx_, 1), "y": round(cy_, 1), "food": r.randint(3, 7), "coin": r.randint(2, 9),
                "hun": 0, "mood": 0, "know": [], "notes": [], "mem": [], "allies": [], "votes": 0,
            })
        self.by_id = {a["id"]: a for a in self.agents}
        self.day, self.slot = 0, 0
        self.news = ["A quiet week begins in Emberfall."]
        self.state = {"fire": None, "shortage": False, "election": None, "festival": False}
        self.rumours = {}  # text -> set of ids
        self.granary = 150
        self.buildings = [{"k": "campfire", "x": 50, "y": 50, "name": "First Fire", "d": 0}]
        self.projects = {}  # id -> {k,name,by,votes,lab,need,st}
        self.pn = 0
        self.era = 0
        self.ledger_build = 0
        self.era_log = [[0, 0, 0]]
        self.burned = []
        self.done_now = []
        self.ledger = {"calls": 0, "tin": 0, "tout": 0, "treason": 0, "cost": 0.0, "decisions": 0,
                       "routine": 0, "fails": 0, "retries": 0}
        self.tally = {}
        self.voted = set()

    # ---------- crises ----------
    def inject(self, kind, arg=None):
        r = self.rng
        if kind == "rumour":
            src = r.choice(self.agents)
            self.rumours.setdefault(arg, set()).add(src["id"])
            src["know"].append(arg)
            src["notes"].append(f"you suspect {arg}")
            return f"{src['n']} ({src['id']}) starts whispering that {arg}."
        if kind == "fire":
            cand = [b for b in self.buildings if b["k"] not in ("campfire",)] or self.buildings
            pref = [b for b in cand if b["k"] == arg]
            b = (pref or cand)[-1]
            self.state["fire"] = {"b": b, "hp": 50, "ttl": 2}
            self.granary = max(0, self.granary - 60)
            return f"FIRE! The {b['k']} '{b['name']}' is burning!"
        if kind == "shortage":
            self.state["shortage"] = True
            for a in self.agents:
                a["food"] = max(0, a["food"] - 2)
                a["hun"] = min(9, a["hun"] + 2)
            return "FOOD SHORTAGE: bread prices triple; families go hungry."
        if kind == "election":
            cands = sorted(self.agents, key=lambda a: (len(a["allies"]), a["coin"]), reverse=True)[:2]
            if len(cands) < 2:
                cands = self.agents[:2]
            self.state["election"] = [c["id"] for c in cands]
            self.tally = {c["id"]: 0 for c in cands}
            self.voted = set()
            return (f"ELECTION for mayor: {cands[0]['n']} the {cands[0]['j']} ({cands[0]['id']}) vs "
                    f"{cands[1]['n']} the {cands[1]['j']} ({cands[1]['id']}). Vote today!")
        if kind == "festival":
            self.state["festival"] = True
            return "HARVEST FESTIVAL at the market: music, ale and a feast!"
        return ""

    # ---------- construction ----------
    def count(self, k):
        return sum(1 for b in self.buildings if b["k"] == k)

    def unlocked(self, job):
        need = UNLOCK.get(job)
        return need is None or self.count(need) > 0

    def can_propose(self):
        return [k for k, (_, e, _) in BUILD.items() if e <= self.era]

    def housed(self):
        return sum(1 for a in self.agents if a.get("home"))

    def propose(self, a, k, name, events):
        same = [pid for pid, p in self.projects.items() if p["k"] == k]
        if k not in self.can_propose():
            return
        if (same and (k != "house" or len(same) >= 3)) or len(self.projects) >= 8:
            if same:
                self.vote_project(a, same[0], events)
            return
        self.pn += 1
        pid = f"P{self.pn}"
        name = " ".join(name.replace('"', "").split()[:4]) or k
        self.projects[pid] = {"k": k, "name": name, "by": a["id"], "votes": {a["id"]}, "lab": 0,
                              "need": int(BUILD[k][0] * (0.8 if k == "house" else SCALE)), "st": "proposed"}
        events.append(["propose", a["id"], pid, f"{k} '{name}'"])
        self.news.append(f"{a['n']} proposes a {k}: '{name}' ({pid}).")

    def vote_project(self, a, pid, events):
        p = self.projects.get(pid)
        if not p:
            return
        p["votes"].add(a["id"])
        if p["st"] == "proposed" and len(p["votes"]) >= 15:
            p["st"] = "building"
            self.news.append(f"Approved: {p['k']} '{p['name']}' ({pid}), {len(p['votes'])} votes. Builders wanted!")
        events.append(["vote", a["id"], pid, ""])

    def build(self, a, pid, events):
        p = self.projects.get(pid)
        if not p or p["st"] != "building":
            return False
        lab = 2 if a["j"] in ("smith", "miner", "guard") and self.unlocked(a["j"]) else 1
        if a["coin"] >= 1:
            a["coin"] -= 1
            lab += 1
        p["lab"] += lab
        self.ledger_build += lab
        if p["lab"] >= p["need"]:
            self.complete(pid)
        return True

    def complete(self, pid):
        p = self.projects.pop(pid)
        k, r = p["k"], self.rng
        n = self.count(k)
        if k == "house":
            unh = {}
            for a in self.agents:
                if not a.get("home"):
                    unh.setdefault(a["h"], []).append(a)
            h = max(unh, key=lambda q: len(unh[q])) if unh else r.choice(list(HAMLETS))
            hx, hy = HAMLETS[h]
            m = sum(1 for b in self.buildings if b["k"] == "house" and b.get("h") == h)
            ang, rad = m * 2.4, 4 + 2.2 * math.sqrt(m)
            x, y = hx + rad * math.cos(ang), hy + rad * math.sin(ang)
            for a in unh.get(h, [])[:40]:
                a["home"] = 1
                a["hx"], a["hy"] = round(x + r.gauss(0, 1.5), 1), round(y + r.gauss(0, 1.5), 1)
        elif k == "walls":
            ang = n * 0.9
            x, y = 50 + 46 * math.cos(ang), 50 + 46 * math.sin(ang)
        else:
            ax, ay = BUILD[k][2]
            x, y = ax + (n % 3) * 5 * (1 if n % 2 else -1), ay + (n // 3) * 5
        b = {"k": k, "x": round(max(3, min(97, x)), 1), "y": round(max(3, min(97, y)), 1), "name": p["name"],
             "d": self.day, "s": self.slot, "by": p["by"], "h": h if k == "house" else None}
        self.buildings.append(b)
        self.news.append(f"BUILT: {k} '{p['name']}' stands complete.")
        self.done_now.append(b)

    def update_era(self):
        c = self.count
        nb = len(self.buildings)
        e = self.era
        if e == 0 and c("house") >= 2 and c("farm") and c("well"):
            e = 1
        if e == 1 and c("granary") and c("market") and nb >= 10:
            e = 2
        if e == 2 and c("hall") and c("temple") and c("school") and nb >= 20:
            e = 3
        if e != self.era:
            self.era = e
            self.news.append(f"NEW ERA: Emberfall is now a {ERAS[e].upper()}!")
            self.era_log.append([self.day, self.slot, e])

    # ---------- prompt encoding ----------
    def near(self, a, pool, k=3):
        return [b["id"] for b in sorted(pool, key=lambda b: (b["x"] - a["x"]) ** 2 + (b["y"] - a["y"]) ** 2)[1:k + 1]]

    def line(self, a, pool):
        kn = ";".join(a["know"][-2:])
        notes = "|".join(a["notes"][-3:])
        al = ",".join(a["allies"][-2:])
        return (f"{a['id']} {a['n']} {a['j']}{'' if self.unlocked(a['j']) else '?'}{'' if a.get('home') else ' homeless'} {a['h']} f{a['food']} c{a['coin']} h{a['hun']} m{a['mood']:+d} "
                f"near={','.join(self.near(a, pool))}" + (f" ally={al}" if al else "")
                + (f" know={kn}" if kn else "") + (f" notes:{notes}" if notes else ""))

    def header(self):
        st = self.state
        s = [f"Day {self.day + 1} {SLOTS[self.slot]}. Era {ERAS[self.era]}. Granary {self.granary}. Housed {self.housed()}/{len(self.agents)}."]
        bc = {}
        for b in self.buildings:
            bc[b["k"]] = bc.get(b["k"], 0) + 1
        s.append("Built: " + ", ".join(f"{v} {k}" for k, v in bc.items()) + ".")
        pj = [f"{pid} {p['k']} '{p['name']}' " + (f"building {p['lab']}/{p['need']}" if p["st"] == "building"
              else f"proposed votes {len(p['votes'])}/15") for pid, p in self.projects.items()]
        s.append("Projects: " + ("; ".join(pj) if pj else "none") + ".")
        s.append("Can propose: " + " ".join(self.can_propose()) + ".")
        if st["fire"]:
            fb = st["fire"].get("b") or {}; s.append(f"FIRE burning at the {fb.get('name') or fb.get('k', 'town')}!")
        if st["shortage"]:
            s.append("Food shortage.")
        if st["election"]:
            s.append(f"Mayoral election today, candidates: {', '.join(st['election'])}.")
        if st["festival"]:
            s.append("Festival at the market.")
        s.append("News: " + " / ".join(self.news[-3:]))
        return " ".join(s)

    # ---------- activation (event-driven) ----------
    def active(self, rate):
        out, routine = [], 0
        crisis = bool(self.state["fire"] or self.state["election"] or self.state["festival"])
        for a in self.agents:
            need = a["hun"] >= 4 or a["notes"] and a["notes"][-1].startswith("!") or a["mood"] <= -2
            if need or crisis and self.rng.random() < 0.6 or self.rng.random() < rate:
                out.append(a)
            else:
                routine += 1
        return out, routine

    # ---------- Ember ----------
    async def call(self, client, batch, sem):
        head = self.header()
        user = head + "\n" + "\n".join(self.line(a, batch) for a in batch)
        body = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                "reasoning": {"effort": "minimal"}, "usage": {"include": True}, "max_tokens": 4000,
                "response_format": {"type": "json_schema", "json_schema": {"name": "d", "strict": True, "schema": SCHEMA}}}
        async with sem:
            for attempt in range(5):
                try:
                    r = await client.post(URL, json=body, timeout=90)
                    if r.status_code in (429, 500, 502, 503, 504):
                        self.ledger["retries"] += 1
                        await asyncio.sleep(1.5 * 2 ** attempt)
                        continue
                    r.raise_for_status()
                    j = r.json()
                    u = j.get("usage", {})
                    L = self.ledger
                    L["calls"] += 1
                    L["tin"] += u.get("prompt_tokens", 0)
                    L["tout"] += u.get("completion_tokens", 0)
                    L["treason"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
                    L["cost"] += float(u.get("cost", 0) or 0)
                    return json.loads(j["choices"][0]["message"]["content"])["d"]
                except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError):
                    self.ledger["retries"] += 1
                    await asyncio.sleep(1.0 * 2 ** attempt)
        self.ledger["fails"] += 1
        return []

    # ---------- apply ----------
    def apply(self, a, d, events):
        act, t, s = d.get("a", "rest"), d.get("t", ""), (d.get("s") or "")[:80]
        tgt = self.by_id.get(t) if t != a["id"] else None
        a["mood"] = max(-3, min(3, a["mood"] + int(d.get("f", 0) or 0)))
        st, tag = self.state, SLOTS[self.slot]
        dest = None
        if act == "propose":
            self.propose(a, t, s, events)
            dest = "hall" if self.count("hall") else (50, 50)
        elif act == "vote" and t in self.projects:
            self.vote_project(a, t, events)
            dest = (50, 50)
        elif act == "build" and t in self.projects:
            if self.build(a, t, events):
                pk = self.projects.get(t, {"k": "house"})["k"]
                dest = BUILD.get(pk, (0, 0, None))[2] or (a["hx"], a["hy"])
                events.append(["build", a["id"], t, s])
        elif act == "work":
            if self.unlocked(a["j"]):
                dest = WORKPLACE[a["j"]]
                a["coin"] += 2
                if a["j"] in ("farmer", "fisher", "baker"):
                    a["food"] = min(9, a["food"] + 1 + min(2, self.count("farm")))
                    self.granary += 1
            else:
                dest = (50, 50)
                a["food"] = min(9, a["food"] + 1)
                a["coin"] += 1
        elif act == "eat":
            if a["food"] > 0:
                a["food"] -= 1
                a["hun"] = 0
            elif a["coin"] >= 2 and self.granary > 0:
                a["coin"] -= 2
                self.granary -= 1
                a["hun"] = 0
            a["mood"] = min(3, a["mood"] + 1)
            dest = "tav"
        elif act in ("trade", "help", "steal", "ally", "gossip") and tgt:
            dest = (tgt["x"], tgt["y"])
            if act == "trade":
                if a["food"] > tgt["food"] and tgt["coin"] >= 1:
                    a["food"] -= 1; tgt["food"] += 1; tgt["coin"] -= 1; a["coin"] += 1
                elif tgt["food"] > 0 and a["coin"] >= 1:
                    tgt["food"] -= 1; a["food"] += 1; a["coin"] -= 1; tgt["coin"] += 1
                tgt["notes"].append(f"{a['id']} traded with you")
                events.append(["trade", a["id"], tgt["id"], s])
            elif act == "help":
                if a["food"] > 0:
                    a["food"] -= 1; tgt["food"] += 1
                tgt["notes"].append(f"!{a['id']} gave you food")
                tgt["mood"] = min(3, tgt["mood"] + 1)
                events.append(["help", a["id"], tgt["id"], s])
            elif act == "steal":
                if tgt["coin"] > 0:
                    tgt["coin"] -= 1; a["coin"] += 1
                tgt["notes"].append(f"!{a['id']} robbed you")
                tgt["mood"] = max(-3, tgt["mood"] - 1)
                events.append(["steal", a["id"], tgt["id"], s])
            elif act == "ally":
                if tgt["id"] not in a["allies"]:
                    a["allies"].append(tgt["id"])
                if a["id"] not in tgt["allies"]:
                    tgt["allies"].append(a["id"])
                tgt["notes"].append(f"!{a['id']} swore alliance")
                events.append(["ally", a["id"], tgt["id"], s])
            elif act == "gossip":
                for rum in a["know"][-2:]:
                    if rum not in tgt["know"]:
                        tgt["know"].append(rum)
                        self.rumours.setdefault(rum, set()).add(tgt["id"])
                        tgt["notes"].append(f"!{a['id']} told you {rum}")
                events.append(["gossip", a["id"], tgt["id"], s])
        elif act == "vote" and st["election"] and t in st["election"] and a["id"] not in self.voted:
            self.tally[t] = self.tally.get(t, 0) + 1
            self.voted.add(a["id"])
            dest = "hall"
            events.append(["vote", a["id"], t, s])
        elif act == "firefight" and st["fire"]:
            st["fire"]["hp"] -= 1
            dest = (st["fire"]["b"]["x"], st["fire"]["b"]["y"])
            events.append(["fire", a["id"], "", s])
        elif act == "protest":
            dest = "hall"
            events.append(["protest", a["id"], "", s])
        elif act == "pray":
            dest = "temp"
        elif act == "feast":
            dest = "mkt"
            a["hun"] = 0
            events.append(["feast", a["id"], "", s])
            a["mood"] = min(3, a["mood"] + 1)
        else:
            dest = "home"
        self.move(a, dest)
        a["mem"].append([self.day, self.slot, act, t if tgt or act == "vote" else "", s])
        # rumour spreads to whoever you speak to, even without gossip, a little
        if s and a["know"] and tgt and self.rng.random() < 0.3:
            rum = a["know"][-1]
            if rum not in tgt["know"]:
                tgt["know"].append(rum)
                self.rumours.setdefault(rum, set()).add(tgt["id"])

    def move(self, a, dest):
        if isinstance(dest, tuple):
            x, y = dest
        elif dest == "home" or dest is None:
            x, y = a["hx"], a["hy"]
        else:
            bk = {"gran": "granary", "mkt": "market", "tav": "tavern", "temp": "temple", "hall": "hall",
                  "forge": "forge", "dock": "docks", "mine": "mine", "farm": "farm", "well": "well"}.get(dest)
            bb = [b for b in self.buildings if b["k"] == bk]
            x, y = (bb[0]["x"], bb[0]["y"]) if bb else (50, 50)
        j = self.rng
        a["x"] = round(max(1, min(99, x + j.gauss(0, 2.2))), 1)
        a["y"] = round(max(1, min(99, y + j.gauss(0, 2.2))), 1)

    def routine(self, a):
        a["mem"].append([self.day, self.slot, "~", "", ""])
        if self.slot == 2:
            self.move(a, "home")
        else:
            self.move(a, WORKPLACE[a["j"]] if self.unlocked(a["j"]) else "farm")
            a["coin"] += 1
            if a["j"] in ("farmer", "fisher"):
                a["food"] = min(9, a["food"] + 1)

    # ---------- one slot ----------
    async def step(self, client, sem, rate=0.5, batch=30, cap=None, injected=()):
        events, news_new = [], []
        self.done_now = []
        n0 = len(self.news)
        for kind, arg in injected:
            news_new.append(self.inject(kind, arg))
        self.news += news_new
        act, routine = self.active(rate)
        if cap:
            act = act[:cap]
        # group by locality so neighbours share a call
        act.sort(key=lambda a: (a["h"], round(a["x"] / 12), a["y"]))
        batches = [act[i:i + batch] for i in range(0, len(act), batch)]
        res = await asyncio.gather(*(self.call(client, b, sem) for b in batches))
        chosen = set()
        for b, ds in zip(batches, res):
            idx = {d.get("i"): d for d in ds if isinstance(d, dict)}
            for k, a in enumerate(b):
                d = idx.get(a["id"]) or (ds[k] if k < len(ds) and isinstance(ds[k], dict) else None)
                if d:
                    self.apply(a, d, events)
                    chosen.add(a["id"])
                    self.ledger["decisions"] += 1
        for a in self.agents:
            if a["id"] not in chosen:
                self.routine(a)
                self.ledger["routine"] += 1
            if self.slot == 2:  # nightly hunger
                a["hun"] = min(9, a["hun"] + (1 if self.count("well") else 2))
                if not a.get("home") and self.rng.random() < 0.3:
                    a["mood"] = max(-3, a["mood"] - 1)
                if a["hun"] >= 6:
                    a["mood"] = max(-3, a["mood"] - 1)
            if len(a["notes"]) > 6:
                a["notes"] = a["notes"][-6:]
        # world resolution
        st = self.state
        if st["fire"]:
            fb = st["fire"]["b"]
            st["fire"]["ttl"] -= 1
            if st["fire"]["hp"] <= 0:
                news_new.append(f"The fire at the {fb['k']} '{fb['name']}' is out, thanks to the bucket lines.")
                st["fire"] = None
            elif st["fire"]["ttl"] <= 0:
                if fb in self.buildings and fb["k"] != "campfire":
                    self.buildings.remove(fb)
                    news_new.append(f"The {fb['k']} '{fb['name']}' burned to the ground.")
                    self.burned.append([self.day, self.slot, fb["k"], fb["name"]])
                st["fire"] = None
        if st["election"] and self.slot == 2:
            w = max(self.tally, key=self.tally.get)
            wa = self.by_id[w]
            news_new.append(f"{wa['n']} the {wa['j']} ({w}) wins the mayoralty, {self.tally[w]} votes to "
                            f"{sum(self.tally.values()) - self.tally[w]}.")
            st["election"] = None
            self.mayor = w
        if st["festival"] and self.slot == 2:
            st["festival"] = False
            news_new.append("The festival ends under lanterns.")
        if st["shortage"] and self.granary > 380:
            st["shortage"] = False
            news_new.append("Grain is flowing again; the shortage eases.")
        if self.slot == 2:
            self.granary += 4 * self.count("farm")
        for n in news_new[len(injected):]:
            self.news.append(n)
        self.update_era()
        frame = {
            "d": self.day, "s": self.slot, "news": self.news[n0:],
            "era": self.era, "nb": len(self.buildings), "housed": self.housed(),
            "bl": [[b["k"], b["x"], b["y"], b["name"]] for b in self.buildings],
            "proj": [[pid, p["k"], p["name"], p["st"], p["lab"], p["need"], len(p["votes"])] for pid, p in self.projects.items()],
            "locked": [j for j in JOBS if not self.unlocked(j)],
            "pos": [[a["x"], a["y"], a["mood"], a["hun"]] for a in self.agents],
            "ev": events[:400], "nev": len(events), "active": len(chosen), "routine": routine,
            "rum": {k: len(v) for k, v in self.rumours.items()}, "gran": self.granary,
            "state": {"fire": [st["fire"]["b"]["x"], st["fire"]["b"]["y"]] if st["fire"] else None, "election": st["election"], "tally": dict(self.tally) if st["election"] or self.slot == 2 else {},
                      "festival": st["festival"], "shortage": st["shortage"]},
            "ledger": dict(self.ledger),
        }
        self.slot += 1
        if self.slot == 3:
            self.slot, self.day = 0, self.day + 1
        return frame


def dump(w, frames, args, t0):
    out = {
        "meta": {"model": MODEL, "agents": len(w.agents), "days_run": w.day + (1 if w.slot else 0),
                 "slots": len(frames), "batch": args.batch, "wall_s": round(time.time() - t0, 1),
                 "jobs": JOBS, "eras": ERAS, "era_log": w.era_log, "burned": w.burned,
                 "mayor": getattr(w, "mayor", None), "build_labour": w.ledger_build},
        "agents": [{k: a[k] for k in ("id", "n", "j", "h")} | {"mem": a["mem"], "allies": a["allies"],
                   "know": a["know"]} for a in w.agents],
        "buildings": w.buildings, "frames": frames, "ledger": w.ledger,
    }
    tmp = Path(args.out + ".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(args.out)


def client():
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                                      "X-Title": "A $1 civilization"},
                             limits=httpx.Limits(max_connections=16))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=1000)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--rate", type=float, default=0.5)
    ap.add_argument("--batch", type=int, default=30)
    ap.add_argument("--conc", type=int, default=8)
    ap.add_argument("--budget", type=float, default=0.95)
    ap.add_argument("--out", default="run.json")
    args = ap.parse_args()
    w = World(args.agents)
    sem = asyncio.Semaphore(args.conc)
    frames, t0 = [], time.time()
    async with client() as c:
        for day in range(args.days):
            for slot in range(3):
                inj = [e for s, e in SCRIPT.get(day, []) if s == slot]
                f = await w.step(c, sem, rate=args.rate, batch=args.batch, injected=inj)
                frames.append(f)
                dump(w, frames, args, t0)
                L = w.ledger
                print(f"D{day + 1} {SLOTS[slot]}: active {f['active']} ev {f['nev']} calls {L['calls']} "
                      f"cost ${L['cost']:.4f} t={time.time() - t0:.0f}s {f['news']}", flush=True)
                if L["cost"] > args.budget:
                    break
            else:
                continue
            break
    dump(w, frames, args, t0)
    print("LEDGER", json.dumps(w.ledger))


if __name__ == "__main__":
    asyncio.run(main())
