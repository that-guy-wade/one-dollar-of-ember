# Sandboxed runner: executes a candidate program's pack() and prints JSON. Run with python -I.
import json, sys, resource
resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
src = open(sys.argv[1]).read()
g = {"__name__": "candidate"}
exec(compile(src, "candidate.py", "exec"), g)
c, r = g["pack"]()
import numpy as np
c = np.asarray(c, float).reshape(-1, 2).tolist(); r = np.asarray(r, float).reshape(-1).tolist()
print("@@RESULT@@" + json.dumps({"centers": c, "radii": r}))
