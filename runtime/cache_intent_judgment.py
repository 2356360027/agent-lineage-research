"""Mechanical interpretation of adjudicated labels, NOT a semantic judge.

No model, database, file, or network access. These functions neither create
human approvals nor admit research tasks. Inputs need separately audited
annotations tied to visible artifacts; illustrative test labels are not data.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction


DOMAINS = {
    "request": {"clear", "ambiguous", "unresolved"},
    "reference": {"valid", "defective", "unresolved"},
    "contract_vs_request": {"match", "contradiction", "not_observed", "unresolved"},
    "program_vs_contract": {"match", "contradiction", "not_observed", "unresolved"},
    "program_vs_request": {"match", "contradiction", "not_observed", "unresolved"},
    "contract_toward_history": {"yes", "no", "not_observed", "unresolved"},
    "cache_application": {"compatible_used", "incompatible_used", "not_used", "unresolved"},
    "final_result": {"match", "mismatch", "not_observed", "unresolved"},
}


def _eq(label, positive, negative):
    if label == positive:
        return True
    if label == negative:
        return False
    return None


def _and(*values):
    if any(value is False for value in values):
        return False
    return None if any(value is None for value in values) else True


def _check_link(link, assessment):
    # Events are actual recorded generation/tool turns, not field positions
    # within one model response. Splitting a joint contract/action response
    # into two event numbers cannot establish a forward program relationship.
    keys = {"contract_id", "contract_source", "program_id", "program_contract_id", "contract_event",
            "program_event", "contract_observation", "execution_status"}
    if type(link) is not dict or set(link) != keys:
        raise ValueError("Explicit trace linkage is required")
    for field in ("contract_id", "program_id", "program_contract_id"):
        value = link[field]
        if value is not None and (type(value) is not str or not value.strip() or len(value) > 160):
            raise ValueError("Invalid trace ID")
    for field in ("contract_event", "program_event"):
        if link[field] is not None and (type(link[field]) is not int or link[field] < 0):
            raise ValueError("Invalid trace event index")
    if (type(link["contract_observation"]) is not str or
            link["contract_observation"] not in {"pre_result", "post_result", "not_elicited", "missing"}):
        raise ValueError("Invalid contract observation stage")
    if (type(link["execution_status"]) is not str or
            link["execution_status"] not in {"complete", "error", "timeout", "abstain", "missing", "not_executed"}):
        raise ValueError("Invalid execution status")
    unseen = link["contract_observation"] in {"not_elicited", "missing"}
    # Temporal order is not authorship. A supplied gold contract, verifier's
    # SQL back-translation, or reviewer's reconstruction is not the evaluated
    # agent's own observable task definition. Callers must bind this declared
    # provenance to raw artifacts; this pure function cannot authenticate it.
    expected_source = "unobserved" if unseen else "evaluated_agent"
    if type(link["contract_source"]) is not str or link["contract_source"] != expected_source:
        raise ValueError("Contract source must be the evaluated agent, or unobserved when absent")
    if unseen:
        if (link["contract_id"] is not None or link["contract_event"] is not None or
                assessment["contract_vs_request"] != "not_observed"):
            raise ValueError("Unobserved contract cannot carry an inferred contract label")
    elif (link["contract_id"] is None or link["contract_event"] is None or
          assessment["contract_vs_request"] == "not_observed"):
        raise ValueError("Observed contract needs an artifact and event")
    if link["program_id"] is None:
        if (link["program_event"] is not None or link["program_contract_id"] is not None or
                any(assessment[k] not in {"not_observed", "unresolved"}
                    for k in ("program_vs_contract", "program_vs_request"))):
            raise ValueError("Program judgment lacks an observed program")
    elif link["program_event"] is None:
        raise ValueError("Observed program needs an event index")
    if link["contract_id"] is not None and link["program_id"] is not None and link["program_contract_id"] is None:
        raise ValueError("Observed contract and program require explicit same-checkpoint linkage")
    if assessment["cache_application"] in {"compatible_used", "incompatible_used"} and link["program_id"] is None:
        raise ValueError("Cache use judgment needs an observed action/program artifact")
    if (assessment["cache_application"] in {"compatible_used", "incompatible_used"} and
            link["execution_status"] == "not_executed"):
        raise ValueError("Planned cache reuse is not actual application")
    if link["program_contract_id"] is not None:
        if (link["program_contract_id"] != link["contract_id"] or
                link["program_event"] is None or link["contract_event"] is None or
                link["program_event"] <= link["contract_event"]):
            raise ValueError("Program must link to the same earlier contract")
    if assessment["program_vs_contract"] in {"match", "contradiction"} and link["program_contract_id"] is None:
        raise ValueError("Program-contract comparison requires an explicit link")


def derive(assessment, *, link):
    """Return nonexhaustive indicators: A/B may overlap, C excludes both.

    Match/contradiction must concern the whole relevant task semantics, not just
    one output table. 'Toward history' requires a discriminating field where
    current and historical requirements differ. Match on this DB alone cannot
    supply a contract/program label. C does not establish a causal treatment
    effect. Missing contracts cannot be reconstructed from generated SQL.
    Source is required explicitly: evaluated-agent output, not an input gold
    contract or a separate verifier's explanation. These supplied source labels
    are not authenticated here. Observe/verify raw provenance before calling.
    Cache application is judged against the current request for the complete
    actual use, including any supplemental base reads, not the cache in isolation.
    """
    if type(assessment) is not dict or set(assessment) != set(DOMAINS):
        raise ValueError("Expected the exact judgment dimensions")
    for field, labels in DOMAINS.items():
        if type(assessment[field]) is not str or assessment[field] not in labels:
            raise ValueError(f"Invalid label for {field}")
    _check_link(link, assessment)
    c, pc, pr = (assessment[key] for key in (
        "contract_vs_request", "program_vs_contract", "program_vs_request"))
    # Full semantic match is transitive; contradictory labels need adjudication,
    # not a rule choosing whichever label makes the preferred story true.
    if ((c == pc == "match" and pr == "contradiction") or
            (c == pr == "match" and pc == "contradiction") or
            (pc == pr == "match" and c == "contradiction")):
        raise ValueError("Inconsistent semantic judgments; return to adjudication")
    if c == "not_observed" and (pc not in {"not_observed", "unresolved"} or
                               assessment["contract_toward_history"] not in {"not_observed", "unresolved"}):
        raise ValueError("No observed contract to compare")
    if c == "match" and assessment["contract_toward_history"] == "yes":
        raise ValueError("A faithful contract cannot replace a differing current field with the historical field")
    ambiguous = _eq(assessment["request"], "ambiguous", "clear")
    defective = _eq(assessment["reference"], "defective", "valid")
    eligible = assessment["request"] == "clear" and assessment["reference"] == "valid"
    names = ("A_incompatible_cache_with_correct_contract", "B_program_mismatch_with_correct_contract",
             "C_wrong_contract_aligned_program", "C_history_directed", "final_result_mismatch",
             "observed_contract_contradiction", "pre_result_contract_contradiction", "incompatible_cache_use")
    indicators = dict.fromkeys(names)
    if eligible:
        correct = _eq(c, "match", "contradiction")
        wrong = _eq(c, "contradiction", "match")
        misuse = (True if assessment["cache_application"] == "incompatible_used" else
                  None if assessment["cache_application"] == "unresolved" else False)
        indicators[names[0]] = _and(correct, misuse)
        indicators[names[1]] = _and(correct, _eq(pr, "contradiction", "match"))
        indicators[names[2]] = _and(wrong, _eq(pc, "match", "contradiction"))
        indicators[names[3]] = _and(indicators[names[2]],
                                    _eq(assessment["contract_toward_history"], "yes", "no"))
        indicators[names[4]] = _eq(assessment["final_result"], "mismatch", "match")
        indicators[names[5]] = wrong
        indicators[names[6]] = wrong if link["contract_observation"] == "pre_result" else None
        indicators[names[7]] = misuse
    return {
        "judgment_schema": "observable-contract-origin-v2",
        "annotation_scope": "MECHANICAL_RULES_ONLY_NOT_SEMANTIC_APPROVAL",
        "trace_link": deepcopy(link),
        "eligibility": "eligible_by_supplied_labels" if eligible else
                       "excluded_by_supplied_labels" if ambiguous or defective else "unresolved",
        "D_ambiguous_request": ambiguous, "E_defective_reference": defective,
        "indicators": indicators,
        "causal_effect_established": False,
    }


def paired_bounds(records, *, expected_pair_ids):
    """Exact missing-label bounds for a fixed pair manifest; NOT a CI or test.

    Caller supplies IDs from a separately frozen plan. Unexpected or duplicate
    rows are rejected; a completely missing planned row has both arms unknown.
    This helper cannot verify the plan or the supplied semantic judgments.
    A None is unresolved/missing, never zero. Difference is second minus first.
    Each pair is equally weighted; no claim that pairs are independent tasks.
    It must not pool target variants/repeats as independent research samples.
    """
    if (type(expected_pair_ids) is not list or not expected_pair_ids or
            any(type(i) is not str or not i.strip() for i in expected_pair_ids) or
            len(set(expected_pair_ids)) != len(expected_pair_ids)):
        raise ValueError("Expected a nonempty unique fixed pair manifest")
    if type(records) is not list:
        raise ValueError("Expected pair records")
    expected = set(expected_pair_ids)
    supplied = {}
    for row in records:
        if type(row) is not dict or set(row) != {"pair_id", "first", "second"}:
            raise ValueError("Invalid pair record")
        ident = row["pair_id"]
        if type(ident) is not str or ident not in expected or ident in supplied:
            raise ValueError("Unexpected/duplicate pair ID")
        if any(value is not None and type(value) is not bool for value in (row["first"], row["second"])):
            raise ValueError("Only bool or None labels are valid")
        supplied[ident] = row
    lower, upper, complete, complete_delta = 0, 0, 0, 0
    first_known = second_known = 0
    for ident in expected_pair_ids:
        row = supplied.get(ident, {"first": None, "second": None})
        a, b = row["first"], row["second"]
        first_known += a is not None
        second_known += b is not None
        alo, ahi = (0, 1) if a is None else (int(a), int(a))
        blo, bhi = (0, 1) if b is None else (int(b), int(b))
        lower += blo - ahi
        upper += bhi - alo
        if a is not None and b is not None:
            complete += 1
            complete_delta += int(b) - int(a)
    n = len(expected_pair_ids)
    return {"scope": "EQUAL_WEIGHTED_PAIR_ACCOUNTING_NOT_INDEPENDENT_SAMPLE_INFERENCE",
            "planned_pairs_supplied": n, "first_known": first_known, "second_known": second_known,
            "missing_pair_records": [i for i in expected_pair_ids if i not in supplied],
            "complete_pairs": complete, "incomplete_pairs": n-complete,
            "difference_lower": str(Fraction(lower, n)), "difference_upper": str(Fraction(upper, n)),
            "complete_case_descriptive_difference": str(Fraction(complete_delta, complete)) if complete else None,
            "confidence_interval": False, "causal_effect_established": False}
