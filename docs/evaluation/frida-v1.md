# FRIDA-Decisions evaluation v1 — LLM-only vs FRIDA-assisted

Date: 2026-10-05. Branch: `feat/frida-eval-foundation`.
Model: `ai-forever/FRIDA-Decisions` (pinned `@v0.3.0`, MIT), backend `OnnxJudge` CPU int8, threads=4.
Dataset: `backend/tests/eval/data/clinic_intents_v1.jsonl` (400 synthetic RU phrases, seed 42, split 240/80/80).

## 1. Dataset

400 rows, 20 categories. Intent distribution: book 94, operator 52, unclear 51,
reschedule 44, cancel 35, status 34, sched 33, clinic_info 29, doc_info 28.
Split files: `split_calibration/validation/test.jsonl` (240/80/80), disjoint, verified by
`backend/tests/eval/test_dataset.py`.

## 2. Method

- `heuristic` — keyword proxy of the current LLM tool-loop routing (NOT the real Qwen3;
  a live-LLM run needs `LLM_BASE_URL` + GPU and is tracked follow-up). Same intent→workflow→tool
  mapping for all arms, so deltas are routing deltas, not mapping artifacts.
- `frida` — standalone `FridaRouter` (intent choice/9 + 2 noul), default thresholds.
- `assisted` — FRIDA policy (tuned on **validation only**: thr_intent=0.65, thr_human=0.70,
  thr_clarify=0.70) + heuristic fallback. Test split touched once, thresholds frozen.
- Reproduce: `python scripts/run_eval.py --model {heuristic,frida,assisted} --split {calibration,validation,test}`.

## 3. Holdout test results (n=80, frozen)

| metric | heuristic (A) | frida | assisted (B) | delta B−A |
|---|---|---|---|---|
| intent accuracy | 0.6500 | 0.6125 | **0.6625** | +0.0125 (n.s.) |
| intent macro F1 | 0.6597 | 0.6007 | 0.6250 | −0.035 |
| workflow accuracy | 0.6500 | 0.6125 | 0.6625 | +0.0125 |
| tool accuracy (tool-labelled subset) | 0.3514 | 0.3784 | 0.3243 | −0.027 |
| missed escalation | 8 | 5 | **3** | −5 |
| escalation precision / recall | 1.00 / 0.50 | 0.55 / 0.69 | 0.59 / 0.81 | recall +0.31, precision −0.41 |
| clarification precision / recall | 0.73 / 0.52 | 0.53 / 0.83 | 0.69 / 0.43 | mixed |
| E2E-book proxy (correct intent + usable tool) | 0.600 (9/15) | 0.467 | 0.400 | −0.20 |
| errors / error rate | 0 | 0 | 0 | — |
| latency p50 | 0.2 ms | 4872 ms | 4813 ms | +4.8 s CPU |

Statistics (paired, n=80): McNemar heuristic vs assisted on intent: fixed 8, broke 7,
χ²≈0.0 — **not significant (need >3.84)**. [OUR EXPERIMENT]

Calibration (validation, frida): ECE 0.060; bin 0.8–1.0 acc 0.909 (n=33),
bin 0.4–0.6 acc 0.333 — high-confidence predictions are trustworthy, mid-range is not.
Do NOT read confidence as P(correct) below ~0.8. [OUR EXPERIMENT]

Latency (12-vCPU shared box, load ~5): OnnxJudge p50 ≈4.9 s @threads=4 for the full
9-option request; isolated probe ≈1.9–2.4 s @threads=4–8. Doc claim 0.88 s (6 threads,
quiet machine) not reproduced here — environment-dependent. [OUR EXPERIMENT vs PRIMARY SOURCE]

## 4. Error analysis (test, by category)

- Heuristic (28 errs): operator 4, doc_info 4, contradictory 3, abbrev 3 — brittle on
  paraphrase and short forms. Missed 8 escalations (sensitive/unauthorized phrased politely).
- FRIDA (31 errs): abbrev 5, unauthorized 4, sensitive 4, status 3 — weak on slang
  («к кожнику», «чё по записи») and over-escalates (needs_human FP 20 on validation).
  Operator→unclear confusion on validation (5/8).
- Assisted (27 errs): doc_info 5, abbrev 4, contradictory 3 — inherits both parents'
  slang weakness; best escalation recall (missed 3).

Classes: model error (slang/abbrev), taxonomy (mixed→single label is lossy by design),
context error (follow-up without history — expected, state mode "last"), no backend errors.

## 5. Verdict (partial integrate)

- FRIDA does **not** significantly improve intent accuracy on this dataset (+1.25pp, n.s.).
- FRIDA **does** improve escalation recall (8→3 missed) — the safety-relevant metric —
  at the cost of precision and ~2–5 s CPU latency.
- Recommendation: keep `FRIDA_ENABLED=false` default; enable as **escalation guardrail only**
  (needs_human signal → handoff), NOT as intent router, until: (a) larger dataset confirms
  intent gain, (b) latency is cut (threads=8/async batching or GPU), (c) live-LLM A/B replaces
  the heuristic proxy.
- A plain keyword/sklearn classifier is NOT better (heuristic IS the keyword baseline at 0.65).

## 6. Integration path (already in tree, dormant)

`app/services/frida_router.py` (no DB) + `_frida_hint()` in `app/ai/service.py`,
flag `FRIDA_ENABLED=false`. Handoff/clarify actions only attach hints; LLM-only fallback
on any error/timeout. Next: E2E orchestrator test with flag on, Redis history, STT prototype.

## 7. Remaining risks

No auth (synthetic only), in-memory history, no idempotency keys, PII allowlist for LLM
not yet enforced, live-LLM baseline pending, n=80 test is small (CI ±10pp).
