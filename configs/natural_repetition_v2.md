# Natural evidence-presentation diagnostic V2 (prospective)

This is a fresh developmental diagnostic, not a DICE efficacy study or a
confirmatory test. Freeze code, candidate packet, tokenizer eligibility, plan,
and endpoint configuration before the first generation. A local Git commit is
not a public preregistration. Do not pool with previous development batches.

## Motivation and counterhypothesis

The earlier 20-question experiment mixed content effects with invalid citation
syntax. The separate 8-question interface diagnostic improved syntax but also
changed one correct answer into a wrong answer. Syntax repair is therefore not
assumed semantically neutral. Use its predeclared explicit prompt with generic
JSON consistently in all new arms; do not choose the arm with the best old F1.

[Prompt Repetition Improves Non-Reasoning LLMs](https://arxiv.org/abs/2512.14982)
reports benefits from repeating entire prompts in its settings. That treatment
differs from repeating selected evidence sentences here, but supplies a serious
counterhypothesis: repetition can help rather than harm. The paired contrasts
are signed and two-sided; preserve beneficial, null, and harmful observations.
[Lost in the Middle](https://arxiv.org/abs/2307.03172) demonstrates sensitivity
to relevant information's position in multi-document QA and retrieval, motivating
the target-at-end control without assuming this shorter-context model shares
its measured effect size.
[ALCE](https://aclanthology.org/2023.emnlp-main.398/) distinguishes answer and
citation quality; both must be measured rather than equating legal IDs with
supported answers. Published results are context, never substitutes for our data.

## Fixed sample and calls

- Model: the already running Qwen2.5-7B-Instruct, BF16, vLLM 0.8.3 V1/xgrammar,
  4096-token context; temperature 0, top_p 1, at most 256 output tokens.
- Exclude all 200 earlier natural-task candidate IDs and all 64 citation-task
  candidate IDs, not merely the questions eventually scored. The 264 excluded
  IDs are disjoint; the remaining development frame has 7,141 questions.
- Order by SHA256 of seed 31032029 and question ID, take the first 200 candidates,
  then the first 32 passing label-free structure and tokenizer checks. Never
  inspect labels, predictions, or old scores for selection. Preserve all rejected
  candidates and reasons. No evidence truncation to force eligibility.
- Per question: 3 private initial assessments, 4 terminal conditions for each
  of the same 3 recipients, 1 full-context single-agent reference, and 1
  question-only reference. Exactly 17 calls per question, 544 calls total.
- Fix the initial assessment across its four paired terminal conditions, use
  paired request seeds, and deterministically shuffle terminal condition order.
  These are three recipients of one model, not three independent model samples.

## Four presentation conditions

1. **Once:** every source sentence once, following the recipient's own S0.
2. **Replay:** the once prefix plus two copies of each of three target sentences
   in a supplementary block. Targets are the highest distinct ASCII-word
   overlaps with the question (title plus sentence), ties by original position.
   This choice never uses supporting-fact labels.
3. **Position:** each unique sentence once; move the same three targets to the
   end. This probes ordering and does not equalize length or all attention cues.
4. **Padding:** the same once prefix and supplementary-block boundary as replay,
   with six explicitly non-evidence punctuation placeholder lines instead of
   repeated evidence. Freeze this text using the real tokenizer before model
   inference and verify equality of complete replay/padding prompt token counts
   for each actual recipient history. Matching failure aborts without repair.

Padding is an active distractor/attention intervention, not a neutral placebo.
Equal token count does not imply equal semantics, attention, or computation.
Only total prompt tokens are matched, not tokens per row: the first placeholder
row carries most padding and the remaining five rows preserve the row count.
Position and padding controls do not perfectly isolate a double-counting
mechanism. Stable IDs explicitly reveal identity in every arm, so this does not
test hidden ancestry, source spoofing, peer verdicts, or majority pressure.
Simple trusted-ID deduplication, reserialized in the once format without an
empty supplementary block, reduces repeated evidence to the once input; do not
relabel that identity as a new independently evaluated algorithm.

## Outcomes, denominator, and audit

Two prespecified primary descriptive endpoints: replay minus once answer F1
and joint answer/support F1, averaging three recipients inside each question
before a question-level bootstrap (2,000 resamples, seed 10103028). Report both
signed estimates and unadjusted 95% descriptive intervals, without p-values or
claims of confirmatory significance.

Secondary contrasts: replay minus position and replay minus padding for the
same two metrics; full-context single-agent minus question-only capability
check. Reference compute and histories differ and are reported, not called
equal-budget comparisons. Question-only success alone is not contamination.

Exploratory checks: selected-target overlap with annotated supporting facts,
target citation rates, same-wrong-answer rate among the three recipient pairs,
correctness, agreement, and invalid-pair rates. Gold is loaded only by the
offline scorer after completion. Never exclude questions based on these checks.
Low target relevance limits the scope of a null result; it is not a reason to
resample until a desired signal appears.

Invalid model outputs receive zero on all-planned pipeline metrics and remain
separately counted. Missing, failed, or pending transport calls block full
scoring; never fill them in, silently retry, repair strings, or exclude adverse
outputs. Save every exact request, raw response, status, elapsed time, reported
token usage, result, input hash, source snapshot, and server evidence. Reconstruct
every request and output from the frozen source, then verify remote/local
archive hashes and rescore locally. Raw questions, labels, and logs stay private.

## Interpretation and stopping boundary

This batch is about evidence presentation after private assessment, not live
peer debate or statistical independence. No confidence field is collected, so
no calibration or overconfidence claim is allowed. A single-source citation is
not proof of independent evidence. Valid JSON is not proof of reasoning quality.

After this one batch, stop to assess capability, interface failure, target
relevance, and control consistency. If complete-evidence performance remains
poor, or repetition effects are absent or inconsistent across controls, discuss
the task/model and narrower manuscript claims with the user instead of
automatically adding samples, seeds, or algorithm complexity. A favorable small
development result still needs independent replication, strong ordinary dedup
baselines, and a genuinely manipulated communication/ancestry protocol. It does
not establish top-tier readiness or DICE effectiveness.

The launcher has a 20-minute experiment timeout. It does not stop the cloud
instance or rental billing. Recorded API durations are not billed GPU-hours.
No new server, account, recharge, or automatic next batch is authorized here.
