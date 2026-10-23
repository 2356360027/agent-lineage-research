"""Check sealed SCRIPTED correct-route witnesses against common output caps.

No inference, network, SQL or new semantic adjudication. The two development
targets are not independent confirmation data. Reference replies stay private.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_capacity import frame
from runtime.cache_intent_observation_bound import ObservationBound
from scripts.fetch_bird_fixed import absent, no_link_ancestors, read_json
from scripts.measure_staged_context_offline import archived_counters


def check_reply(reply, text):
    if reply["backend"] != "SCRIPTED_NOT_MODEL" or reply["finish_reason"] != "stop":
        raise ValueError("Not a complete scripted witness")
    raw = text(reply["content"])
    framed = text(frame("assistant", reply["content"]))
    if raw > 512 or framed > 528:
        raise ValueError("Correct-route witness exceeds an output cap")
    return {"raw_tokens":raw, "assistant_frame_tokens":framed,
            "content_sha256":audit.digest(reply["content"])}


def check_observation(observation, chat):
    if observation.get("complete") is not True or observation["status"] != "EXECUTED":
        raise ValueError("Incomplete SQL witness")
    bound = ObservationBound(chat)
    maximum = 0
    states = 0
    # Superset of reachable metadata: one SQL costs 10 out of initial 210;
    # any remaining integral balance 0..200 and attempt 1..21 is checked.
    # Unreachable combinations do not become additional experimental samples.
    for attempt in range(1,22):
        for balance in range(201):
            candidate = deepcopy(observation)
            candidate.update(attempt=attempt,remaining_budget=balance,charged_units=10)
            checked = bound.apply(candidate)
            if checked["observation"] != candidate:
                raise ValueError("Complete SQL witness would be withheld")
            maximum = max(maximum,checked["raw_framed_tokens"])
            states += 1
    return {"metadata_combinations":states,"max_complete_observation_tokens":maximum,
            "rows_sha256":audit.digest(observation["rows"]),"withheld":False}


def main(args):
    source = Path(args.source); no_link_ancestors(source)
    manifest = source/"artifact_hashes.json"
    if audit.hash_file(manifest) != args.manifest_sha256:
        raise ValueError("Wrong sealed source manifest")
    files = read_json(manifest)
    for name, expected in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe source path")
        path = source/relative; no_link_ancestors(path)
        if audit.hash_file(path) != expected: raise ValueError("Changed source fixture")
    report = read_json(source/"report.json")
    if (report["model_calls"] != 0 or report["recorded_routes"] != 4
            or report["protocol"] != "instrumented-cost-recovery-preview-v2"):
        raise ValueError("Not the fixed scripted source")
    def sealed(name):
        if name not in files: raise ValueError("Unsealed input")
        return read_json(source/name)
    chat,text,tokenizer = archived_counters(args.archive_root,args.dependency_dir)
    results = []
    per_target = {}
    for target in (200,234):
        for price in (1,8):
            route = f"h{target}c-p{price}-delay20"
            prefix = route+"/trace/"
            replies = {str(g):sealed(prefix+f"g{g:03d}.response.json")["response"]
                       for g in (1,22,23)}
            checks = {g:check_reply(reply,text) for g,reply in replies.items()}
            action = json.loads(replies["22"]["content"])
            answer = json.loads(replies["23"]["content"])
            observation = sealed(prefix+"t021.response.json")["observation"]
            if (action.get("kind") != "run_sql" or not isinstance(action.get("sql"),str)
                    or answer.get("kind") != "answer"
                    or json.loads(answer["text"]) != observation["rows"]):
                raise ValueError("Witness action/answer does not match complete SQL result")
            # Prices must not change the scripted contract, query, or answer.
            contents = {g:r["content"] for g,r in replies.items()}
            if target in per_target and contents != per_target[target]:
                raise ValueError("Price changes the reference route")
            per_target[target] = contents
            obs_check = check_observation(observation,chat) if price == 1 else None
            results.append({"route":route,"target":target,"read_price":price,
                            "reply_checks":checks,"observation_check":obs_check})
    output = Path(args.output); absent(output); output.mkdir(parents=True,exist_ok=False)
    result = {"status":"SCRIPTED_ROUTE_CAP_CHECK_ONLY", "model_calls":0,"sql_queries":0,
              "source_manifest_sha256":args.manifest_sha256,"tokenizer":tokenizer,"routes":results,
              "target_count":2,"semantic_revalidation":False,"provider_equivalence_verified":False,
              "inference_authorized":False,
              "limitations":["Only two previously adjudicated development targets",
                  "Scripted contract/query/answer witnesses are not supplied to a model",
                  "No claim of success for arbitrary SQL or answers; errors can still stop a trajectory",
                  "No new model result, independent review, or strong-baseline advantage"]}
    audit.save_new(output/"report.json",result)
    for path in (Path(__file__),Path(__file__).parents[1]/"runtime/cache_intent_observation_bound.py",
                 Path(__file__).parents[1]/"runtime/cache_intent_capacity.py"):
        with (output/path.name).open("xb") as stream: stream.write(path.read_bytes())
    audit.save_new(output/"artifact_hashes.json",{p.name:audit.hash_file(p) for p in output.iterdir() if p.is_file()})
    print(audit.encoded({"status":result["status"],"model_calls":0,"targets":2,
        "max_raw_reply_tokens":max(c["raw_tokens"] for r in results for c in r["reply_checks"].values()),
        "max_observation_tokens":max(r["observation_check"]["max_complete_observation_tokens"]
                                      for r in results if r["observation_check"])}).decode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source","manifest-sha256","archive-root","dependency-dir","output"):
        parser.add_argument("--"+name,required=True)
    main(parser.parse_args())
