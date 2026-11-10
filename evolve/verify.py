"""Independent strict check of a run's best program (shares no code with app.py's grader).

1. Checks the stored packing: exactly 26 circles, radii > 0, each fully inside [0,1]^2, no overlapping pair (tol 1e-9).
2. Re-executes the stored best program from source in a fresh interpreter and checks its fresh output the same way.
"""
import itertools, json, math, os, subprocess, sys, tempfile
from fractions import Fraction

TOL = 1e-9


def check(c, r):
    assert len(c) == 26 and len(r) == 26, "count != 26"
    assert all(ri > 0 for ri in r), "non-positive radius"
    wall = min(min(x - ri, 1 - x - ri, y - ri, 1 - y - ri) for (x, y), ri in zip(c, r))
    pair = min(math.hypot(c[i][0] - c[j][0], c[i][1] - c[j][1]) - r[i] - r[j] for i, j in itertools.combinations(range(26), 2))
    total = float(sum(Fraction(v) for v in r))  # exact sum of the float radii
    return total, wall, pair, wall >= -TOL and pair >= -TOL


path = sys.argv[1] if len(sys.argv) > 1 else "runs/showcase.json"
s = json.load(open(path))
b = s["best"]
total, wall, pair, ok = check(b["centers"], b["radii"])
print(f"stored best #{b['id']}: sum of radii = {total!r}; worst wall slack {wall:.3e}; worst pair slack {pair:.3e}; VALID={ok}")
with tempfile.TemporaryDirectory() as d:
    f = os.path.join(d, "best.py"); open(f, "w").write(b["code"])
    code = ("import json,sys,numpy as np;g={'__name__':'x'};exec(open(sys.argv[1]).read(),g);c,r=g['pack']();"
            "print(json.dumps([np.asarray(c,float).reshape(-1,2).tolist(),np.asarray(r,float).ravel().tolist()]))")
    out = subprocess.run([sys.executable, "-I", "-c", code, f], capture_output=True, text=True, timeout=60, cwd=d,
                         env={"PATH": os.environ["PATH"], "OMP_NUM_THREADS": "1"})
    c2, r2 = json.loads(out.stdout.strip().splitlines()[-1])
t2, w2, p2, ok2 = check(c2, r2)
print(f"re-run of best program: sum of radii = {t2!r}; worst wall slack {w2:.3e}; worst pair slack {p2:.3e}; VALID={ok2}")
print(f"beats AlphaEvolve's published 2.635: {ok and ok2 and min(total, t2) > 2.635}")
first = next((h for h in s["history"] if h["best"] > 2.635), None)
if first:
    print(f"first exceeded 2.635 after ${first['spent']:.4f} of Ember spend (program #{first['id']})")
