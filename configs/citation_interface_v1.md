# Citation-interface diagnostic, prospective v1

**Historical failed protocol:** `citation-interface-001` was rejected on its
first request by the serving V1 engine. Do not launch this v1 protocol again.
See [failure record](run_records/citation_interface_001.json). The design below
is retained as the pre-attempt plan, not a claim of successful execution.

This is an engineering prerequisite for a natural-task multi-agent study, **not
DICE validation, a new method, or evidence of statistical independence**.

## Why this batch exists

The completed `natural-repetition-001` has 67 invalid outputs out of 220. A
post-hoc audit found 38 outputs whose only validation problem was a displayed ID
such as `[d0s0]` copied literally instead of the required `d0s0`. The original
prompt did not illustrate this distinction. Those data, scores, code and
unfavorable comparisons remain unchanged. We will not rehabilitate their
primary estimates by stripping brackets or filling missing fields.

## Fixed prospective comparison

Eight fresh questions, eight calls per question, 64 calls total:

| Factor | Levels |
| --- | --- |
| Supplied evidence | Full ten-paragraph distractor context; question only |
| Prompt | Original byte-identical prompt; append explicit bare-ID/required-field examples |
| Decoding | Original generic JSON object; schema-guided JSON with explicit `outlines:no-fallback` |

Keep the same Qwen2.5-7B-Instruct content fingerprint, greedy sampling, 256-token
output cap and 4096-token context limit. Shuffle the eight cells per question;
all eight use a matched seed. There is no agent history, peer verdict,
repetition manipulation, model judge, repaired output, or additional call.

The prompt revision combines two clarifications; their separate contributions
are not identified. Decoder conditions compare stacks, not a pure schema
effect. The installed vLLM 0.8.3 rejects enum/maxItems with
`xgrammar:no-fallback`; this was discovered by source inspection before any
experimental inference. Outlines is selected explicitly rather than allowing
fallback. All actual schemas are compiled offline before the plan is frozen.
The generic JSON baseline retains its original default-backend request.

## Sampling and data isolation

Use the same checksummed HotpotQA validation parquet. Exclude **all 200** prior
candidate IDs, then SHA256-order the remaining 7205 IDs using seed 31032028.
Prepare 64 candidates and separate scoring labels. Select the first eight
complete ten-document contexts that fit both prompts, reserving 256 output
tokens. The runtime reads only projected question/source text, never answers
or support labels. No selection uses correctness, citation outcomes or gold.

This excludes overlap with our previous candidate frame; it says nothing about
model pretraining contamination. Full-versus-question-only differences do not
by themselves diagnose memorization.

## Contract and scoring

Both conditions retain the original strict parser: exactly `answer` and
`citations`, answer <=160 UTF-8 bytes, at most eight actually visible sentence
IDs, stop finish reason. An empty answer with an empty citation array is valid;
an empty object is not. No coercion, wrapper removal, key insertion or retries.
The schema does not use character `maxLength` as a substitute for byte length.
Question-only uses a zero-length array constraint, never an invalid empty enum.

Predeclared descriptive primary contrasts, on full-context questions:

1. Explicit minus original prompt: strict-valid rate under generic JSON.
2. Guided minus generic JSON: strict-valid rate under explicit prompt.

Report all eight cells' all-planned answer EM/F1, supporting-fact F1, joint F1,
validity and failure types. Report all paired prompt/decoder quality contrasts
and context controls as exploratory, not selectively favorable tests. Every
format-invalid completed output earns zero in all-planned pipeline metrics;
infrastructure failure or missing records block complete scoring, never get
imputed. Preserve raw requests/responses and exact schedules locally.

Bootstrap whole matched questions (eight units), 2000 resamples, seed 10103027.
Intervals are unadjusted descriptive summaries, not confirmation, equivalence,
novelty evidence, or permission to count 64 independent questions. Valid ID
membership is not citation entailment or support quality.

## Decision boundary

No automatic extension or effect-based early stop. Finish the frozen batch and
verify its backup first. If the explicit generic interface yields at least 7/8
valid full-context outputs and no remaining bracket-only mismatch there, it is
eligible for a **separate** new-question communication experiment; this is an
engineering readiness threshold, not evidence of a reliable universal parser.
Do not choose an interface based on which yields the most favorable paper
effect. If it fails, inspect specific failures and discuss the next revision;
do not launch another sequence of outcome-targeted fixes automatically.

Guided decoding is an engineering comparator, not the paper's proposed novelty.
The subsequent research must test real recipient-specific information reuse
against ordinary deduplication, memoization, no-communication and strong
single-agent baselines, preserving distinct useful same-source facts. A clean
interface cannot establish any of those claims by itself.

## Primary references

- [ALCE (Gao et al., EMNLP 2023)](https://aclanthology.org/2023.emnlp-main.398/): answer correctness and citation quality are distinct evaluation targets.
- [JSONSchemaBench (Geng et al., 2025)](https://arxiv.org/html/2501.10868v3): evaluate schema compliance/coverage and downstream quality separately.
- [Let Me Speak Freely? (Tam et al., EMNLP Industry 2024)](https://aclanthology.org/2024.emnlp-industry.91/): format restrictions can affect reasoning quality; effects are task/configuration dependent.
- [vLLM 0.8.3 structured outputs](https://docs.vllm.ai/en/v0.8.3/features/structured_outputs.html): guided JSON and explicit no-fallback backend settings.

This design is result-inspired and developmental, not a preregistered
confirmatory study or retrospective validation of the previous batch.
