# One Dollar of Ember

Built in one hour at the OpenRouter × Meta Build Day (SF, 2026-11-09) for the **$1 challenge**. Every inference call goes to **Muse Ember** (`meta/muse-ember`) through OpenRouter. Each project spends about $1 of Ember, and every number below was measured from OpenRouter's own `usage.cost` per call.

Both projects use the same idea. Ember is cheap enough to call thousands of times, so we spend those calls on *many small decisions*, and ordinary code does everything else for free.

## 1. Ember Civilization: 1,000 minds build a city (`civ/`)
About 1,000 townspeople, each driven by Ember, live through simulated days. They propose, vote on and staff construction projects (houses, farms, a granary, a bridge, a temple, a school, walls), and the settlement grows from a campfire camp into a city. Elections, festivals, fires and rumours emerge from their choices. Click any villager to open their story: mood, hunger, beliefs, allies, and every choice they made, in their own Ember-written words, day by day. Each villager is an individual: name, job, hamlet, food, coins, hunger, mood, home, allies, the rumours they believe, recent notes and nearby neighbours all go into the line Ember sees about them. Routine moments are handled by plain code at no AI cost.
- **Ember for decisions only.** Each Ember call decides for 25–30 townspeople at once, using a compact JSON state, and only people with something to decide are included. Deterministic code handles movement, the economy and what buildings do.
- **Muse Image, generated once.** `meta/muse-image` drew a cohesive sprite set (16 sprites at $0.01 each) that is reused for the whole run, plus a few "chronicle" paintings of key moments.
- **Measured:** **1,000 townspeople, 19 simulated days, 42,215 Ember decisions in 1,433 calls for $0.955** (2.41M tokens in, 1.48M out, 0 failed calls), which is **about 44,000 decisions per $1**. The settlement went from Camp to Village on Day 2, Town on Day 4 and **City on Day 14**, finishing with **52 buildings and all 1,000 townspeople housed**. Along the way an election, a poisoned-well rumour (315 believers) and a 'the mayor hoards grain' rumour all emerged. Muse Image spend was $0.21 in total (16 sprites plus chronicle paintings, at $0.01 each).

## 2. Bonus: Ember Evolve, AlphaEvolve on a $1 budget (`evolve/`)
Ember is the mutation operator in an evolutionary program search, and a local grader is the judge. The task is the circle-packing benchmark from DeepMind's AlphaEvolve paper: place 26 non-overlapping circles in a unit square to maximise the sum of their radii.
- **Measured:** **769 Ember-written programs for $0.99** (1.65M tokens in, 1.72M out, 0 failed calls) took a hand-written seed from **0.624 to 2.635983** (4.2×). That **beat the original AlphaEvolve paper's 2.635862 after 54 mutations and $0.053**, and **matched the best-known 2.635983 after 125 mutations and $0.13**. This is not a new record. It shows that $1 of the smallest Muse model, given a free grader, reaches a frontier result.
- **Checked strictly.** Every candidate is checked for containment and overlap at a tolerance of 1e-9. The final packing was re-verified independently: the smallest boundary slack is 9e-12 and the smallest pairwise gap is 1.8e-11, with no tolerance needed. The winning program (#457) is a real 57-line SciPy optimiser (SLSQP plus an LP for the radii) with no hardcoded coordinates.
- **Contamination check.** This benchmark is public, so we tested Ember zero-shot (5 attempts, no evolution): its best was 2.616, 2 of 5 programs crashed, and none contained memorised coordinates. The evolution loop supplied the last 0.02. Ember clearly knows the standard method (SLSQP), though, so read this as fast reproduction, not discovery.
- **References:** the original AlphaEvolve paper reports 2.635862. The best known result is 2.635983 (AlphaEvolve v2, ThetaEvolve, TTT-Discover). OpenEvolve's example run reached 2.634292.

## Run
Put `OPENROUTER_API_KEY=...` in `.env`, install the requirements, and start the server: Civilization on :8102, Evolve on :8101. Each saved run replays with zero tokens.

```bash
pip install -r requirements.txt
echo 'OPENROUTER_API_KEY=sk-or-...' > civ/.env; cp civ/.env evolve/.env
cd civ && uvicorn server:app --port 8102     # replays run.json; 'Live mode' runs new ticks on Ember
cd evolve && uvicorn app:app --port 8101    # replays runs/showcase.json
```

Demo video: see the v1.0 release.
