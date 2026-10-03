# Citation interface V2: prospective V1-compatible diagnostic

This is an engineering diagnostic, not a DICE efficacy experiment. It does not
modify, repair, rescore, or pool previous natural-repetition outputs.
`citation-interface-001` failed at the first HTTP request (400 backend mismatch),
with zero model completions. V2 preserves that failed batch and reuses its
label-free candidate frame openly rather than treating it as prior outcome data.

## Fixed design

Eight questions; 2 contexts (full/question-only), 2 prompts (original/explicit),
2 decoders (generic JSON/bounded citation grammar): exactly 64 calls. Original
messages and generic JSON request are unchanged. The explicit prompt jointly
clarifies bare citation IDs and the required empty-answer object. Both are kept
as independent cells, with deterministic within-question order and paired seed.
Same Qwen2.5-7B-Instruct weights, BF16, greedy decoding, 256 output tokens, 4096
context limit. Eligibility uses complete-context token length, never gold labels.

Actual server remains vLLM 0.8.3 **V1 / xgrammar**. V1 requires request backend to
match the engine exactly; Outlines and `xgrammar:no-fallback` do not satisfy the
existing engine configuration. V2 uses exact `xgrammar` and explicit EBNF instead
of assuming JSON Schema `maxItems` was enforced by installed XGrammar 0.1.17.

The grammar accepts both field orders, standard JSON whitespace/answer escapes,
zero to eight citations, and only visible canonical bare IDs. It requires exactly
`answer` and `citations`. Canonical key/ID spellings are narrower than all possible
JSON Unicode-escaped spellings; this is a serialization intervention, not a pure
prompt comparison. The 160 UTF-8-byte answer limit is still checked after decoding.
Duplicate visible citations remain syntactically legal but gain no duplicate
support credit under the unchanged scorer. No decoder guarantees factual truth.

## Preflight and integrity

Before inference, exercise installed ChatCompletionRequest conversion, actual
V1 Processor admission and XgrammarBackend compilation on CPU. Record every
positive/negative fixture text, expected decision, tokenizer tokens, first
rejection and termination, along with backend/package/source hashes. Require
all fixtures to pass. This finite suite is not a proof for every token path.
Independently freeze and validate the actual server startup line (V1, xgrammar,
4096 tokens); record live process command and model metadata at launch.
Plan reproduction, model content hash, source bundle and external plan hash must
match. Raw requests and responses persist before/after each call; never retry,
overwrite, truncate, or repair. Failed/pending infrastructure calls block scoring.

## Endpoints and decision rule

Two descriptive primary contrasts (question-level paired differences):

1. Full-context format-validity: explicit minus original prompt under generic JSON.
2. Full-context format-validity: bounded grammar minus generic JSON under explicit prompt.

Report all eight cells, answer EM/F1, support metrics and joint F1, diagnostic
failures, real token usage and recorded API duration. Invalid completed outputs
score zero; no valid-only primary analysis. Eight questions, not 64 outputs,
are bootstrap units; intervals are descriptive and unadjusted.

Prespecified engineering readiness: full-context explicit/generic JSON has at
least 7/8 format-valid outputs and no bracket-only mismatch. This permits planning
a separate real communication experiment; it neither proves answer quality nor
automatically starts another batch. If only guided decoding passes, disclose the
changed output intervention and discuss the next protocol rather than repairing
old results. No DICE claim follows from a successful format repair.

## Literature basis

- [vLLM V1 guide](https://docs.vllm.ai/en/v0.8.3/getting_started/v1_user_guide.html):
  V1 feature/backend behavior differs from generic/V0 paths; actual installed
  source is additionally inspected and hashed.
- [JSONSchemaBench](https://arxiv.org/abs/2501.10868): separate constraint
  coverage, efficiency and output quality; compiling a schema alone is insufficient.
- [ALCE](https://aclanthology.org/2023.emnlp-main.398/): answer correctness and
  citation quality are distinct dimensions. Here HotpotQA's fixed official
  answer/support metrics are used, not a substituted ALCE model-based evaluator.
