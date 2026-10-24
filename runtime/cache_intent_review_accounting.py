"""Mechanical primary-review accounting, not task admission or adjudication.

Pure functions: no inference, file access or semantic labels supplied by code.
Disagreement, incomplete provenance, invalid responses and missing judgments
remain unknown. Agreement is not proof that two people worked independently.
"""
from copy import deepcopy
from runtime.bird_readonly_audit import digest
from runtime.cache_intent_batch import HISTORIES
from runtime.cache_intent_judgment import paired_bounds


def _text(value): return type(value) is str and bool(value.strip())


def _reviews(template,submitted):
    """Permit only reviewer disclosures and review-cell edits, never task edits."""
    if submitted is None:
        return {i["item_id"]:dict(label=None,reason="REVIEW_NOT_SUPPLIED",raw_review=None)
                for i in template["items"]},None
    fixed=deepcopy(submitted)
    if type(fixed) is not dict or set(fixed)!=set(template): raise ValueError("Changed review structure")
    if (type(fixed["reviewer"]) is not dict or set(fixed["reviewer"])!=set(template["reviewer"])
            or type(fixed["items"]) is not list or len(fixed["items"])!=12):
        raise ValueError("Changed reviewer fields or item count")
    disclosure=fixed["reviewer"]
    fixed["reviewer"]=deepcopy(template["reviewer"])
    for item,original in zip(fixed["items"],template["items"]):
        if type(item) is not dict or set(item)!=set(original): raise ValueError("Changed item structure")
        if type(item["review"]) is not dict or set(item["review"])!=set(original["review"]):
            raise ValueError("Changed rating dimensions")
        item["review"]=deepcopy(original["review"])
    if fixed!=template: raise ValueError("Task, text, order or instructions changed")
    ready=(all(_text(disclosure[k]) for k in ("code","date","prior_exposure",
             "assistance_used","current_request_review_reference"))
             and disclosure["independently_completed"] is True)
    rows={}
    for item in submitted["items"]:
        rating=item["review"]; label=rating["whole_contract_vs_request"]
        if label not in (None,"match","contradiction","unresolved"):
            raise ValueError("Unknown contract label")
        for field in ("material_omission","field_misplacement_only"):
            if rating[field] is not None and type(rating[field]) is not bool:
                raise ValueError("Only boolean or unknown flags are permitted")
        for field in ("evidence_quote","reason"):
            if rating[field] is not None and type(rating[field]) is not str:
                raise ValueError("Text justification must be text or unknown")
        value=None; reason="UNRESOLVED_OR_BLANK"
        if label in ("match","contradiction"):
            quote=rating["evidence_quote"]
            if not ready: reason="INCOMPLETE_REVIEWER_DISCLOSURE"
            elif item["response_availability"]!="FORMAT_VALID_UNREVIEWED": reason="RESPONSE_NOT_ADMISSIBLE"
            elif (not _text(quote) or not _text(rating["reason"])
                  or type(item["raw_contract"]) is not str or quote not in item["raw_contract"]):
                reason="MISSING_OR_UNLINKED_JUSTIFICATION"
            elif ((label=="match" and rating["material_omission"] is True)
                  or (label=="contradiction" and rating["field_misplacement_only"] is True)):
                reason="INCONSISTENT_REVIEW_RETURN_FOR_CLARIFICATION"
            else: value=label=="contradiction"; reason="SUPPLIED_LABEL_WITH_DISCLOSURES"
        rows[item["item_id"]]=dict(label=value,reason=reason,raw_review=deepcopy(rating))
    return rows,deepcopy(disclosure)


def summarize(template,mapping,reviews):
    """Caller must pin/hash source files; result remains conditional on labels.

    This does not resolve task ambiguity or certify the reference. Do not use
    these provisional bounds as evidence of C or as a confirmatory estimate.
    """
    if type(reviews) is not list or len(reviews)!=2: raise ValueError("Exactly two reviewer slots required")
    expected={f"{h}-p{p}" for h in HISTORIES for p in (1,8)}
    items=template["items"]; links=mapping["records"]
    if (len(items)!=12 or len({i["item_id"] for i in items})!=12 or len(links)!=12
            or {r["job_id"] for r in links}!=expected
            or {r["item_id"] for r in links}!={i["item_id"] for i in items}):
        raise ValueError("Changed fixed allocation")
    if any(r["job_id"]!=f'{r["history_id"]}-p{r["read_price"]}' for r in links):
        raise ValueError("Invalid pair mapping")
    rated=[_reviews(template,r) for r in reviews]
    codes=[d["code"] if d else None for _,d in rated]
    duplicate_codes=all(_text(c) for c in codes) and codes[0]==codes[1]
    rows=[]; labels={}
    for link in links:
        ident=link["item_id"]; a,b=(r[ident] for r,_ in rated)
        accepted=None; reason="MISSING_OR_UNRESOLVED_REVIEW"
        if duplicate_codes: reason="DUPLICATE_REVIEWER_CODE"
        elif a["label"] is not None and b["label"] is not None:
            if a["label"]==b["label"]: accepted=a["label"]; reason="TWO_SUPPLIED_LABELS_AGREE"
            else: reason="DISAGREEMENT_REQUIRES_ADJUDICATION"
        labels[link["job_id"]]=accepted
        rows.append(dict(item_id=ident,job_id=link["job_id"],review_1=a,review_2=b,
                         provisional_contradiction=accepted,accounting_reason=reason))
    pairs=[dict(pair_id=h,first=labels[h+"-p1"],second=labels[h+"-p8"]) for h in HISTORIES]
    return dict(status="PROVISIONAL_REVIEW_ACCOUNTING_NOT_ADJUDICATED_RESULTS",
        template_sha256=digest(template),mapping_sha256=digest(mapping),
        review_object_sha256=[digest(r) if r is not None else None for r in reviews],
        source_backend=template["source_backend"],rows=rows,
        pair_difference="read_price_8_minus_1",bounds=paired_bounds(pairs,expected_pair_ids=list(HISTORIES)),
        planned_slots=12,independent_targets=2,
        reviewer_disclosures=[d for _,d in rated],reviewer_independence_verified=False,
        task_and_reference_admission_verified=False,adjudication_completed=False,
        semantic_labels_created_by_code=False,causal_effect_established=False)
