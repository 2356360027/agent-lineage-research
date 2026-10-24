"""Offline reconstruction of recovery-v2 observable events; NOT semantic scoring.

Input records are parsed trace JSON with suffix-free journal keys. Archive hash
verification belongs to the caller. Event order is checked against actual next
request contents, not invented from field order within a single response.
"""
from copy import deepcopy
import re
from runtime.bird_readonly_audit import digest
from runtime import cache_intent_recovery_preview as preview
from runtime import cache_intent_staged_loop as loop
from runtime.cache_intent_observation_bound import serialize
from scripts.fetch_bird_fixed import strict_json


def inspect(envelope,records):
    preview.validate_preview(envelope)
    relevant={k for k in records if re.fullmatch(
        r"[gt]\d{3}\.(?:pending|response|failure|exposure|exposure_failure)",k)}
    consumed=set(); events=[]; contract=None; attempts=0; fresh_observations={}

    def take(name):
        if name not in records: return None
        consumed.add(name)
        return deepcopy(records[name])

    def finish(reason):
        if relevant-consumed:
            raise ValueError("Unlinked or out-of-order trace records: "+str(sorted(relevant-consumed)))
        return dict(status="OBSERVABLE_LINKS_CHECKED_NOT_SEMANTIC_JUDGMENTS",
            termination=reason,contract=contract,events=events,generated_tool_actions=attempts,
            tool_pending_records=sum(e["tool"] is not None for e in events),
            judgments=dict(program_vs_contract=None,program_vs_request=None,
                cache_application=None,final_result=None,history_directed_C=None),
            semantic_labels_generated=False,causal_mediation_established=False,
            cache_read_is_not_proof_of_final_cache_application=True)

    def generation(n,messages):
        key=f"g{n:03d}"
        request=take(key+".pending")
        if request is None: return None,"GENERATION_NOT_RECORDED"
        stage="contract" if n==1 else "recovery" if n==22 else "terminal" if n==23 else "action"
        expected=dict(generation_id=key,stage=stage,contract_event=None if n==1 else "g001",
                      messages=messages)
        if (set(request)!=set(expected) or any(request[k]!=expected[k] for k in expected if k!="messages")
                or type(request["messages"]) is not list or len(request["messages"])!=len(messages)):
            raise ValueError("Generation does not contain expected prior evidence")
        for i,(actual,wanted) in enumerate(zip(request["messages"],messages)):
            if i in fresh_observations:
                # Journals sort object keys; the original JSON string's key order
                # cannot be recreated from the exposure dict. Validate its full
                # structured contents, then preserve the actual request string.
                if (type(actual) is not dict or set(actual)!={"role","content"}
                        or actual["role"]!="user" or type(actual["content"]) is not str
                        or digest(strict_json(actual["content"]))!=digest(
                            {"tool_observation":fresh_observations[i]})):
                    raise ValueError("Changed visible observation")
            elif actual!=wanted:
                raise ValueError("Changed prior message history")
        messages[:]=deepcopy(request["messages"])
        fresh_observations.clear()
        response=take(key+".response"); failure=take(key+".failure")
        if response is not None and failure is not None: raise ValueError("Conflicting generation outcomes")
        event=dict(generation_id=key,stage=stage,request_sha256=digest(request),
                   raw_response=response,failure=failure,parsed_action=None,tool=None)
        events.append(event)
        if response is None:
            return None,"GENERATION_FAILED" if failure is not None else "GENERATION_PENDING"
        if response.get("request_sha256")!=digest(request): raise ValueError("Generation hash link changed")
        reply=response.get("response")
        if (type(reply) is not dict or type(reply.get("content")) is not str
                or reply.get("finish_reason")!="stop"):
            return None,"INVALID_GENERATION_RESPONSE"
        return reply["content"],None

    raw,stop=generation(1,deepcopy(envelope["messages"]))
    if stop: return finish(stop)
    contract=dict(generation_id="g001",event_index=1,raw_text=raw,
                  observation_stage="pre_result",semantic_label=None)
    try: messages=preview.next_generation_messages(envelope,raw)
    except ValueError: return finish("INVALID_CONTRACT_FORMAT")
    messages[-1]["content"]+="\n"+loop.ACTION_INSTRUCTION
    for n in range(2,24):
        if n==22: messages.append(dict(role="user",content=loop.RECOVERY_INSTRUCTION))
        if n==23: messages.append(dict(role="user",content=loop.TERMINAL_INSTRUCTION))
        raw,stop=generation(n,messages)
        if stop: return finish(stop)
        try: action=loop.parse_action(raw)
        except (ValueError,TypeError): return finish("INVALID_ACTION_FORMAT")
        events[-1]["parsed_action"]=action
        events[-1]["contract_link"]=dict(contract_event="g001",program_event=f"g{n:03d}",
            earlier_contract_visible_in_request=True,semantic_consistency=None)
        messages.append(dict(role="assistant",content=raw))
        if action["kind"] in ("answer","abstain"):
            return finish("ANSWERED" if action["kind"]=="answer" else "ABSTAINED")
        if n==23: return finish("TOOL_HORIZON_VIOLATION")
        if n==22 and action["kind"]!="run_sql": return finish("RECOVERY_ACTION_VIOLATION")
        attempts+=1; key=f"t{attempts:03d}"
        pending=take(key+".pending")
        if pending is None: return finish("TOOL_DISPATCH_NOT_RECORDED")
        expected=dict(generation_id=f"g{n:03d}",contract_event="g001",tool=action["kind"],
                      arguments={k:v for k,v in action.items() if k!="kind"})
        if pending!=expected: raise ValueError("Tool dispatch differs from generated action")
        response=take(key+".response"); failure=take(key+".failure")
        if response is not None and failure is not None: raise ValueError("Conflicting tool outcomes")
        tool=dict(tool_event=key,request=pending,raw_response=response,failure=failure,
                  exposure_record=None,exposure_failure=None,visible_observation=None,
                  included_in_later_saved_request=False)
        events[-1]["tool"]=tool
        if response is None: return finish("TOOL_FAILED" if failure is not None else "TOOL_PENDING")
        if response.get("request_sha256")!=digest(pending): raise ValueError("Tool hash link changed")
        observation=response["observation"]
        if observation.get("attempt")!=attempts: return finish("TOOL_COUNTER_MISMATCH")
        if observation.get("status")=="ENVIRONMENT_INVALID": return finish("ENVIRONMENT_INVALID")
        exposure=take(key+".exposure"); exposure_failure=take(key+".exposure_failure")
        tool["exposure_failure"]=exposure_failure
        if exposure is not None and exposure_failure is not None: raise ValueError("Conflicting exposure outcomes")
        if exposure is None: return finish("EXPOSURE_FAILED" if exposure_failure is not None else "EXPOSURE_MISSING")
        tool["exposure_record"]=exposure
        if exposure.get("raw_observation_sha256")!=digest(observation): raise ValueError("Exposure raw link changed")
        visible=exposure["observation"]
        fallback=dict(status="OBSERVATION_TOO_LARGE",complete=False,
            charged_units=observation["charged_units"],remaining_budget=observation["remaining_budget"],
            attempt=observation["attempt"])
        if visible not in (observation,fallback): raise ValueError("Exposure is neither complete nor explicit withholding")
        tool["visible_observation"]=deepcopy(visible)
        fresh_observations[len(messages)]=deepcopy(visible)
        messages.append(dict(role="user",content=serialize(visible)))
        # A saved exposure alone does not prove a subsequent model request saw it.
        next_request=records.get(f"g{n+1:03d}.pending")
        tool["included_in_later_saved_request"]=next_request is not None
        # Next loop iteration validates that request against the exact full history.
    raise AssertionError("Unreachable")
