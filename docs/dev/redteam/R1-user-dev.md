# Red Team R1 — Pipelines Package Design Review
**Persona:** Dev (postdoc, CLI-comfortable, reproducibility-focused)
**Doc reviewed:** `docs/dev/PIPELINES_PACKAGE.md`
**Date:** 2026-05-16

---

OK. I've been handed the design doc and told this is what the new pipelines GUI is going to look like. I have 45 minutes. Let me actually read this instead of skimming the intro and nodding.

Six analysis cards: vSNP, Kraken, AMR, Sourmash, MLST, "future." The whole pitch is that instead of three separate bespoke wrappers drifting apart, you have one `AnalysisPrimitive` contract and every tool conforms to it. Front-ends just call `.run()` and render `.web()`. Sounds clean in theory. Let me find where it breaks.

---

**First thing I want to know: can I run this from CLI without touching the GUI?**

Section 8 says "This is non-negotiable: nobody should have to fire up a GUI to run AMRFinder on one assembly." And they give the command:

```bash
python -m pipelines.amrfinder --fasta IN109.fasta --threads 16 --out amr_results/
```

OK. That's the right answer. But — and here's where I pull the thread — does that CLI invocation write provenance? The `run()` method in the sketch calls `write_provenance()`, and the `__main__` block presumably just instantiates the class and calls `.run()`. So... yes, it should. But the sketch in §5 shows the `__main__` block as a one-liner that's implied, not actually written out. **[Gap: the doc doesn't show the `__main__` body.** I have to trust that whoever writes `amrfinder.py` actually calls `.run()` rather than calling `subprocess` directly. If they forget to call `write_provenance()` inside `__main__`, the CLI path is provenance-dark and I'll never know.]

That's a real risk with an ABC pattern. The abstract method enforces `.provenance()` exists. It does not enforce that it gets *called* during a CLI run.

---

**Second: what command did the GUI actually run?**

Looking at the provenance schema in §6. There's a `"command"` field in `record.json`. The example shows:

```
"command": "amrfinder -n /.../IN109.fasta --plus --threads 16 -o IN109.amr.tsv"
```

**[Smell something off]** That's a fully-reconstructed command string, not the actual `argv` list. Look at the `provenance()` method in the AMRFinder sketch:

```python
"command": "amrfinder -n ... --plus",
```

That's a **hardcoded stub** — it has literal `...` in it. The actual `cmd` list built in `.run()` is:

```python
cmd = ["amrfinder", "-n", str(self.FASTA), "--plus",
       "--threads", str(self.threads), "-o", str(tsv)]
```

So the real command exists in `cmd` but `provenance()` returns a hand-written string that doesn't reference `cmd` at all. Those two can drift. If I add `--db_path` as a new argument tomorrow, it goes in `cmd`, the tool uses it, and the provenance record never mentions it. **[Found a gap]** The design doesn't thread `cmd` through to `provenance()`. It should store `self._cmd` after building it and return `" ".join(self._cmd)` from `provenance()`. This is fixable in about two lines but the sketch as written has the bug.

I'd also want `command.sh` in the provenance dir (which §6 does specify as a separate file) — and if that's generated from the same stub, it has the same problem.

---

**Third: what's the source of truth — `samples.json` or some database?**

Section 3 is clear: `samples.json` is the shared knowledge base. No separate database. The `Project.record_finding()` method merges into it. Good. No hidden state.

But now I want to know: **what happens if I edit `samples.json` manually?**

The doc doesn't say. The `Project` helper reads it, presumably on each call. If I go in and fix a typo in `"organism": "Mammaliicoccus sciuri"` (I notice the doc has it spelled correctly, which is more than I can say for most people), the next card that calls `applicable(sample_context)` should pick up the change because it reads from disk. **[Found an answer — or rather, inferred one]** The design seems to want `samples.json` to be human-editable because it's just JSON on disk. That's the right call for a lab setting. What's missing is any locking story: if the GUI and I edit the file simultaneously, one of us clobbers the other. `record_finding()` does a merge into `samples.json[sample][primitive]` — is that a read-modify-write? If yes, it's a race condition if two primitives run in parallel on the same project. The doc is silent on this.

---

**Fourth: what if the conda env isn't there?**

Section 7 says each tool gets its own conda env. `run_in_conda_env("amrfinder", cmd, log_path=log)` is the call. Open question 6 in §11 mentions `PrimitiveSetupError` as the thing that should be raised when the tool is missing. But it's listed as an *open question* — "define `PrimitiveError` and `PrimitiveSetupError` in `common/contract.py`." That means right now, if `amrfinder` env doesn't exist, the behavior is undefined. `run_in_conda_env()` presumably calls `conda run -n amrfinder ...` and gets a non-zero exit code. Whether that surfaces as a clean error badge or a hung process or an uncaught exception depends entirely on what's in `runners.py` — which is a stub.

**[Smell]** The most user-hostile failure mode in any pipeline GUI is the silent hang when a dependency is missing. The design knows this (it lists it as open question 6) but hasn't solved it yet. For my NAHLN AMR batch, if the `amrfinder` env is misconfigured, I want to see "AMR: setup error — env 'amrfinder' not found" as a badge immediately, not a process that runs for 30 minutes before timing out.

---

**Fifth: crash mid-run. What state am I in?**

Say AMRFinder runs on 8 samples. Sample 4 crashes. The `.provenance/exit_code` sentinel exists — if the primitive wrote it before crashing, I can see exit_code=1. But the design doesn't say how the GUI handles partial completion. Does the "Run AMR" button re-run all 8? Just the failed ones? Is there a way to skip already-completed samples?

**[Gap]** The `applicable()` classmethod checks if an `assembly_fasta` exists, not if a *completed* AMR result exists. So re-running will overwrite successful outputs too. For a 5-second-per-genome tool like AMRFinder that's fine. For something like assembly (SPAdes, tens of minutes), silently re-running because one sample failed would be genuinely painful. The `ensure_assembly()` helper in `Project` does check if the FASTA already exists and reuses it — so that case is handled. But there's no equivalent `ensure_amr()` pattern for the other primitives. You'd have to add that yourself, or the primitives always re-run from scratch.

---

**Sixth: is the provenance schema enough to reproduce a run in 6 months on a different machine?**

Let me be honest here. The schema has: tool version, DB version, input SHA256, command, host, timestamp, conda env name. That's actually pretty good. Better than most GUIs I've used.

What's missing: the conda env *contents* — not just `"env": "conda:amrfinder"` but a `conda list --export` snapshot. If I try to reproduce a run from 2026 in 2027, I know I need `amrfinder` env, but I don't know what version of `libssl` or `python` was in it. For the tool version itself (AMRFinder 4.2.7) it's captured. But AMRFinder's underlying BLAST or HMMER versions aren't. For AMR gene calls, those auxiliary versions probably don't matter. For something like Kraken's classification, they might.

**[Verdict: good enough for this lab's use case, not enough for publication-grade reproducibility.]** If we were submitting to Genome Medicine I'd want a container hash. For NAHLN surveillance reporting, knowing the DB version and tool version is what the inspector is going to ask for anyway. So: acceptable.

---

**Seventh: the "single-file Apptainer swap" claim.**

Section 7: "Switching to Apptainer later is a single-file change." That's `common/runners.py`. The claim is that changing one function from `conda run -n amrfinder ...` to `apptainer exec amrfinder.sif ...` is sufficient.

I'm skeptical. It's a single-file change for the *invocation*. But Apptainer containers bind-mount paths. If my project lives in `/home/vxk1/projects/` and the container doesn't have that path bound, the FASTA won't be visible to `amrfinder` inside the container. That's not a `runners.py` problem — that's an invocation argument problem that requires knowing the project root. `runners.py` would need to either always bind `projects_root` (meaning it needs to know the config), or the caller passes bind mounts. Neither is "single-file." **[Smell: the claim is directionally true but oversimplified.]**

---

**Eighth: what happens to `posthoc/snp_analysis.py`?**

I went and read this file. It's a substantial standalone: ~392 lines, its own `argparse` CLI, its own `run()` function, its own provenance-ish `write_stats()`. It does lineage detection by string-matching sample names, runs `snp-dists`, generates KDP plots and closest-neighbor plots. It's doing real science, not boilerplate.

The doc doesn't mention it. Not once. It says "kraken.py — MOVE from kapurlab/kraken_id_parse_gui" and "sourmash.py — extract from current ad-hoc usage" but says nothing about `posthoc/snp_analysis.py`. **[Found a gap]** If this is going to live as a primitive, it's a `vsnp_posthoc` or `snp_distances` primitive. If it stays as-is, it's a one-off that doesn't conform to the contract and won't get badges, won't write provenance in the new schema, won't be callable from CLI via `python -m pipelines.snp_distances`. The doc is silent. This feels like an oversight — someone designed the package around the AMR workflow and didn't look at what was already in `posthoc/`.

More specifically: `snp_analysis.py` has `write_stats()` that writes its own `stats.json` — not the T-07 `record.json` schema from §6. So if I run the posthoc analysis, I get different provenance format than if I run AMRFinder. That's exactly the kind of drift the design is supposed to prevent.

---

**Forming my verdict.**

After 45 minutes: I'd use this for my next AMR batch, but I'd want two things fixed first and one thing documented before I trust the provenance for anything I'm going to share outside the lab.

---

## Verdict (out of character)

- **Would Dev use this?** Yes, with reservations. The CLI-first guarantee (§8) and the provenance schema (§6) are the two things that matter most to a reproducibility-conscious user. Both exist in the design. The reservations are the hardcoded stub in `provenance()["command"]` (real bug, easy fix) and the unresolved error surface for missing envs (open question 6, which needs to be closed before any real batch run).

- **The single most important thing the design got right:** The provenance schema is mandatory and non-optional for every primitive — you can't implement the `AnalysisPrimitive` ABC without providing `.provenance()`. That's the right forcing function. Most GUIs treat provenance as an afterthought. This one treats it as a contract requirement.

- **The single most important thing it got wrong:** The `provenance()["command"]` field in the AMRFinder sketch is a handwritten stub that doesn't reflect the actual `cmd` list built in `.run()`. This is the most dangerous kind of bug because it's invisible — the provenance *looks* complete, the schema validates, but the recorded command is wrong. Fix: store `self._cmd` on the instance during `.run()` and serialize it in `provenance()`.

- **One question Dev would ask in a design review:** What happens to `posthoc/snp_analysis.py`? It's 400 lines of real analysis that already exists in the repo, writes its own stats format, and isn't mentioned anywhere in the design doc. Is it becoming a primitive? Staying as a one-off? If it stays as-is, the design has already failed its stated goal of eliminating format drift on day one.
