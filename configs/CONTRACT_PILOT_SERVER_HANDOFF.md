# Existing server handoff: private 24-call pilot

Prepared 2026-10-13, Hong Kong date. This is deployment software, not a model
result. The user supplied a terminal observation: idle RTX 4090 D (24 GB), Python
3.12.12, torch 2.6.0+cu124, vLLM 0.8.3, transformers 4.57.1, ModelScope 1.34.0;
port 8000 refused a connection. The persistent model index exists at
`/gemini/code/model-cache/Qwen/Qwen2___5-7B-Instruct`. The old project directory
is not a Git checkout. No successful SSH authentication is claimed.

## Boundary

- User-authorized exploratory inference, not weight training or semantic approval.
- Same frozen 24 calls and two development targets from scientific commit
  `74024cf28842902d0ccaf3d02e6c2d0b50de8459`; no new prompts or modified runner.
- Expected model contents come from the privately archived Oct 2 migration record.
  Only six native manifest fields are included; historical GPU/API metadata are
  not presented as current observations. Every model file and package version is
  rechecked before serving. This does not attest loaded model memory.
- Current budget supplied by user: 95 yuan at 2 yuan/hour. Supervisor allocates
  at most 1,800 seconds to preflight, startup and client execution together;
  startup alone has a five-minute cap. Cleanup/archive time is additional.
- Only the locally verified model is served at 127.0.0.1:8000. No installation,
  model download, paid API, SQL execution, SSH retry or new machine.
- Existing compute processes or an occupied port cause refusal. Only child
  process groups created by this supervisor are terminated, on success/failure.
  **Cloud instance billing continues until the user stops it in the platform.**

## Build and private transfer

From the local project root:

```text
python -B -m scripts.build_contract_pilot_bundle
```

The generated `deployment/contract-pilot-74024cf-20261013.pyz` is a standard Python
ZIP application, not a dependency installer. A separate local receipt records
its complete SHA-256 and allowlisted members. It contains the frozen pre-result
prompt material but no participant answers, credentials, weights or full database.
Keep the generated file private; it is intentionally ignored by Git.

Upload the file through the existing platform file interface to `/gemini/code`.
After validating the supplied archive hash, execute ONCE in the platform terminal:

```bash
python -u /gemini/code/contract-pilot-74024cf-20261013.pyz --execute
```

The application creates a new sibling directory and refuses a second extraction
or an existing run. Do not delete a failed attempt to try again. The terminal
must remain connected; no detached execution or background monitoring is promised.
Ctrl+C requests owned-process cleanup and preserves records.

## Logs, failure and recovery

Run output is under the new unpacked directory:
`runtime/runs/server-pilot-20261013-001/`.
It includes model verification, GPU observation, actual launch command and PIDs,
server startup log, experiment log, runner raw responses and failure/coverage
records. The supervisor does not inspect semantic effects to choose sample size.
There is no generation retry, response repair, fallback, overwrite or resumption.

If the global deadline interrupts an in-flight request, its pending/raw records
are retained; a complete runner summary may not exist. Treat it as uncertain,
not an automatically failed prediction, and do not resubmit that call.

After own-process cleanup, a sibling result ZIP and `.archive.json` contain the
downloadable bundle and archive SHA-256. Download and verify these before stopping
or destroying the instance. Raw prompts/responses remain private, not public GitHub.
Only later independent review can support semantic conclusions; this run reports
format/coverage/token diagnostics, not task accuracy or cache-induced intent drift.

## Verification scope

Local extraction/plan checks and mock-based unit tests do not establish remote
vLLM JSON-schema compatibility, successful GPU startup, or a real model outcome.
Those remain unverified until the user runs this handoff and returns the logs.
