# Natural-context repetition diagnostic (developmental)

User approved this direction after the `routing-timing-002` developmental gate
failed. That prior result remains unchanged. This pilot is a prerequisite test
on natural QA, **not a novel DICE efficacy experiment or certified computation
reuse evaluation**.

## Question and scope

Does a specified repeated-presentation policy alter answer and citation quality
when the set of available source sentences is held fixed? It jointly changes
length, position, and salience. A positive or negative result does not by itself
identify statistical double-counting, social conformity, or independence.

Data: HotpotQA distractor development/validation split, fixed Hugging Face mirror
revision `1908d6afbbead072334abe2965f91bd2709910ab`, parquet SHA-256
`c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6`.
Original download host timed out; mirror byte equivalence to the original JSON
has not been claimed. License: CC BY-SA 4.0; Yang et al., EMNLP 2018.
Raw contexts and responses are ignored by Git and retained privately.

## Frozen design

The JSON alongside this document fixes selection seed, generation settings,
call count, replay policy, and analysis. Order all 7,405 IDs by a seeded SHA-256;
screen the first 200. Select the first 20 with ten complete supplied paragraphs
and a conservative native-tokenizer context fit. Do not inspect gold labels or
model performance to choose items. Preserve all eligibility decisions. Report
this as a length-restricted development sample, not full HotpotQA performance.

For each question:

1. Split source sentences round-robin over three recipients. Obtain three
   private S0 assessments with the same answer/citation schema.
2. Each recipient reconsiders its own frozen S0 using all context sentences once.
3. The matched recipient reconsiders the same S0 using the same complete context
   plus two appended copies of three label-free lexical-overlap-selected units.
4. A single-union reference answers with all context and no S0 history.
5. A question-only reference answers without context; no questions are removed
   based on its performance.

Total: 11 real model calls per question, 220 calls; no stochastic repetitions.
Final arm order is seeded/randomized, with paired seeds across once/replay.
The three recipients are not three independent tasks. No LLM adjudicator runs.

Exact source-sentence deduplication of the replay must produce a byte-identical
prompt to the once condition. This is a checked structural equivalence and an
explicit alias of the once output—not three further empirical observations or
a new algorithm. Same-document distinct sentences must survive; identical text
at different source coordinates is not globally merged.

## Outcomes and integrity

Two prespecified **descriptive**, not confirmatory, contrasts: repeated-minus-once
answer F1 and joint F1, averaging three recipients within each question first.
Use question-clustered percentile bootstrap intervals (2,000 resamples), display
all question differences and separate answer/support metrics. Joint F1 is
calculated from joint precision/recall, not the product of component F1 scores.

Model-generated format violations score zero for the all-planned pipeline
endpoint and are separately reported. Infrastructure failures stop the run and
prevent full-run scoring; they are not imputed as incorrect answers. Preserve
every request, response, usage report, and failure. No model retries, output
repairs, silent truncation, result-driven sample changes, or unfavorable-output
deletion. Token/time totals are measured, not expanded into fictional compute
expenses. The lower-cost single-union reference is not compute matched.

This does not test useful intermediate derivations or reuse versus recomputation.
Those require a subsequent algorithm and strong ordinary memoization/dedup
controls, not rebranding bookkeeping. Small/no degradation in this pilot is not
population equivalence; improvement from repetition is possible and must remain
in the report. No automatic scale-up follows this batch.

## Primary sources

- Dataset and license: https://hotpotqa.github.io/
- Pinned mirror: https://huggingface.co/datasets/hotpotqa/hotpot_qa/tree/1908d6afbbead072334abe2965f91bd2709910ab
- Dataset paper: https://aclanthology.org/D18-1259/
- Scoring definitions: https://github.com/hotpotqa/hotpot/blob/master/hotpot_evaluate_v1.py
- Shortcut-risk analysis: https://aclanthology.org/P19-1416/

Qwen training contamination is unknown. Question-only success diagnoses
evidence-free solvability, not proven memorization; matching support annotations
does not certify the causal reasoning path.
