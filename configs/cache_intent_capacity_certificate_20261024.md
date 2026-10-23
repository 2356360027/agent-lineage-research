# Conditional capacity certificate for the recovery-v2 environment

2026-10-24. Local arithmetic/implementation evidence only. No inference, no
server-capacity certification, no causal or method-performance claim.

## Bound and domain

Let F be the token count of the two fixed initial message frames, the three
fixed instruction frames (stage 2, recovery, terminal), and generation prefix.
All are counted with the pinned archived Qwen tokenizer and plain-chat template.
At a final request there can be at most 22 earlier assistant frames and 21 tool
observation frames. With assistant-frame cap 528, observation-frame cap 512 and
final completion reservation 512, the sufficient envelope is

`B = F + 22*528 + 21*512 + 512`.

Across the twelve scope-informed development previews, B ranges 24004–24117
tokens. Thus the minimum margin to a proposed 32768 window is 8651. This is a
conditional whole-history bound, not extrapolation from the longest observed
script. The bound includes both successful and incomplete/error observations
that obey the same exposure limit. It does not bound unexposed private logs.

## Assumptions, evidence and enforcement

The source for tokenizers v0.22.1 extracts non-normalized added tokens before
normalizing the remaining pieces; the tokenizer then pretokenizes and encodes
those pieces. We inspected these primary implementations:
[added vocabulary](https://raw.githubusercontent.com/huggingface/tokenizers/v0.22.1/tokenizers/src/tokenizer/added_vocabulary.rs)
and [tokenizer pipeline](https://raw.githubusercontent.com/huggingface/tokenizers/v0.22.1/tokenizers/src/tokenizer/mod.rs).

For the archived assets the chat delimiters are atomic, non-normalized, not
single-word restricted, and do not strip surrounding whitespace. No added token
contains a newline or straddles the framed-message seam; the model is BPE with
NFC normalization. These properties support decomposition at the chat markers,
not an assumption that arbitrary unframed strings have additive token counts.
The audit checks the pinned version/configuration, marker conditions, and 81
finite boundary regressions including Unicode, whitespace and marker text.
Those examples are regression tests, not an exhaustive proof about Unicode.

`RecoveryCapacity` verifies every actual prompt against its initial messages,
allowed message counts, unique fixed instructions and per-frame limits. It
requires exact equality between whole-chat tokenization and the sum of complete
message-frame counts plus generation prefix. Any mismatch is a retained failure
before dispatch, never an opportunity to truncate or silently switch tokenizers.
It rejects an input/window combination if B exceeds the proposed window.

The adapter keeps the existing 512 raw-output cap and additionally checks a
528-token assistant-frame cap. This 16-token framing allowance is explicit; an
over-cap response is preserved as failure, not shortened. The tool policy uses
an empty-system/user framed count <=512, which conservatively includes the
actual tool-observation frame. Verify this composition with the same assets.
Source/tokenizer changes require renewed admission. These caps limit the
admitted protocol, not the underlying model's general abilities.

## What passed and what did not become true

Five capacity tests, eighteen adapter tests, and nine legacy-loop tests passed.
A new replay used the capacity checker on all 92 requests from four sealed
scripted software trajectories. No model, HTTP, or SQL execution occurred during
this replay. It confirms component linkage and actual framing on those records.

The complete static envelope was computed for all twelve previews. This does
not establish that every valid semantic answer fits 512 tokens, that every SQL
result fits its observation limit, or that a server can serve the proposed
window. Those need correct-route admission and live configuration checks.
The future launcher must install this checker rather than a generic permissive
callback; no ready/approved live launcher is claimed here.

Decision: the local conditional context envelope now passes for the fixed
previews. Proceed to full input/source packaging and task-specific correct-route
cap checks. Do not repeat token-length diagnostics indefinitely or turn this
engineering certificate into a paper novelty claim. The research hypothesis
and strong-baseline incremental benefit remain untested.
