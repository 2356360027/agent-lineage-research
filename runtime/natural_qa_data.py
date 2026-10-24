"""Label-isolated HotpotQA adapter and deterministic, dependency-free scoring.

The prediction projection never reads answer/support annotations. Sentence IDs
identify source locations, not atomic facts or statistically independent sources.
Metric definitions follow HotpotQA's published evaluator, independently
implemented here: https://github.com/hotpotqa/hotpot/blob/master/hotpot_evaluate_v1.py
"""

from collections import Counter
from collections.abc import Iterable, Mapping
import hashlib
import json
import re
import string
from typing import Any


METRIC_NAMES = ("em", "f1", "prec", "recall")
OFFICIAL_METRIC_NAMES = tuple(
    prefix + name for prefix in ("", "sp_", "joint_") for name in METRIC_NAMES
)
_UNIT_FIELDS = {"id", "title", "sentence_index", "text", "text_sha256"}


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _mapping(value: Any, name: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def prediction_view(row: Mapping) -> dict:
    """Project only id/question/context; even malformed gold is never inspected.

    Empty sentences are retained verbatim so official sentence indices never
    shift. Duplicate titles are rejected because support coordinates would be
    ambiguous. Extra row fields, including all labels, are discarded.
    """
    row = _mapping(row, "row")
    item_id = _nonempty_string(row.get("_id"), "_id")
    question = _nonempty_string(row.get("question"), "question")
    context = row.get("context")
    if not isinstance(context, list):
        raise ValueError("context must be a list")
    titles, units = set(), []
    for doc_index, document in enumerate(context):
        if not isinstance(document, (list, tuple)) or len(document) != 2:
            raise ValueError("each context entry must contain title and sentences")
        title = _nonempty_string(document[0], "context title")
        if title in titles:
            raise ValueError(f"duplicate context title: {title!r}")
        titles.add(title)
        sentences = document[1]
        if not isinstance(sentences, list):
            raise ValueError("context sentences must be a list")
        for sentence_index, sentence in enumerate(sentences):
            if not isinstance(sentence, str):
                raise ValueError("every context sentence must be a string")
            units.append({
                "id": f"d{doc_index}s{sentence_index}",
                "title": title,
                "sentence_index": sentence_index,
                "text": sentence,
                "text_sha256": hashlib.sha256(sentence.encode("utf-8")).hexdigest(),
            })
    return {"id": item_id, "question": question, "units": units}


def _coordinate(value: Any) -> tuple[str, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("support coordinate must be [title, sentence_index]")
    title = _nonempty_string(value[0], "support title")
    index = value[1]
    if type(index) is not int or index < 0:
        raise ValueError("support sentence_index must be a nonnegative integer")
    return title, index


def _unit_lookup(view: Mapping) -> dict[str, tuple[str, int]]:
    view = _mapping(view, "prediction view")
    if set(view) != {"id", "question", "units"}:
        raise ValueError("prediction view must contain only id/question/units")
    _nonempty_string(view["id"], "view id")
    _nonempty_string(view["question"], "view question")
    if not isinstance(view["units"], list):
        raise ValueError("view units must be a list")
    lookup, coordinates = {}, set()
    for unit in view["units"]:
        unit = _mapping(unit, "unit")
        if set(unit) != _UNIT_FIELDS:
            raise ValueError("unit fields do not match the prediction whitelist")
        unit_id = _nonempty_string(unit["id"], "unit id")
        if unit_id in lookup:
            raise ValueError(f"duplicate unit id: {unit_id!r}")
        coordinate = _coordinate((unit["title"], unit["sentence_index"]))
        if coordinate in coordinates:
            raise ValueError(f"duplicate source coordinate: {coordinate!r}")
        if not isinstance(unit["text"], str):
            raise ValueError("unit text must be a string")
        expected_hash = hashlib.sha256(unit["text"].encode("utf-8")).hexdigest()
        if unit["text_sha256"] != expected_hash:
            raise ValueError("unit text_sha256 does not match its exact text")
        lookup[unit_id] = coordinate
        coordinates.add(coordinate)
    return lookup


def _validated_gold(gold: Mapping, lookup: Mapping) -> dict:
    gold = _mapping(gold, "gold")
    if set(gold) != {"answer", "supporting_facts"}:
        raise ValueError("gold must contain only answer/supporting_facts")
    if not isinstance(gold["answer"], str):
        raise ValueError("gold answer must be a string")
    if not isinstance(gold["supporting_facts"], list):
        raise ValueError("gold supporting_facts must be a list")
    present, supports = set(lookup.values()), []
    for fact in gold["supporting_facts"]:
        coordinate = _coordinate(fact)
        if coordinate not in present:
            raise ValueError(f"gold support is absent from context: {coordinate!r}")
        supports.append(list(coordinate))
    return {"answer": gold["answer"], "supporting_facts": supports}


def gold_view(row: Mapping, view: Mapping | None = None) -> dict:
    """Extract offline labels and check references, without selecting context."""
    row = _mapping(row, "row")
    if view is None:
        view = prediction_view(row)
    view = _mapping(view, "prediction view")
    if view.get("id") != row.get("_id"):
        raise ValueError("gold row and prediction view IDs differ")
    return _validated_gold({
        "answer": row.get("answer"),
        "supporting_facts": row.get("supporting_facts"),
    }, _unit_lookup(view))


def adapt_hotpot_row(row: Mapping) -> tuple[dict, dict]:
    """Return separate (prediction_view, gold); neither aliases mutable input."""
    view = prediction_view(row)
    return view, gold_view(row, view)


def seeded_order(rows: Iterable[Mapping], seed: int | str) -> list[Mapping]:
    """Order only by SHA256(JSON([seed, id])), then id; do not inspect labels.

    Accept native ``_id`` or projected ``id`` records. Duplicate IDs and
    conflicting dual ID fields fail closed. Records themselves are not copied.
    """
    if type(seed) not in (int, str):
        raise ValueError("seed must be an integer or string")
    keyed, seen = [], set()
    for row in rows:
        row = _mapping(row, "row")
        if "_id" in row and "id" in row and row["_id"] != row["id"]:
            raise ValueError("conflicting _id/id fields")
        item_id = _nonempty_string(row.get("_id", row.get("id")), "row id")
        if item_id in seen:
            raise ValueError(f"duplicate row id: {item_id!r}")
        seen.add(item_id)
        encoded = json.dumps([seed, item_id], ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        keyed.append((hashlib.sha256(encoded).digest(), item_id, row))
    return [entry[2] for entry in sorted(keyed, key=lambda entry: entry[:2])]


def normalize_answer(answer: str) -> str:
    """Hotpot normalization: lowercase, ASCII punctuation, articles, spaces."""
    if not isinstance(answer, str):
        raise ValueError("answer must be a string")
    without_punctuation = answer.lower().translate(str.maketrans("", "", string.punctuation))
    without_articles = re.sub(r"\b(?:a|an|the)\b", " ", without_punctuation)
    return " ".join(without_articles.split())


def _overlap_metrics(matches: int, predicted_count: int, reference_count: int,
                     exact: bool) -> dict[str, float]:
    precision = matches / predicted_count if predicted_count else 0.0
    recall = matches / reference_count if reference_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"em": float(exact), "f1": f1, "prec": precision, "recall": recall}


def answer_metrics(prediction: str, reference: str) -> dict[str, float]:
    """Answer EM and bag-of-token overlap, including special yes/no handling."""
    predicted, expected = normalize_answer(prediction), normalize_answer(reference)
    special_answers = {"yes", "no", "noanswer"}
    if predicted != expected and (predicted in special_answers or expected in special_answers):
        return dict.fromkeys(METRIC_NAMES, 0.0)
    predicted_tokens, expected_tokens = predicted.split(), expected.split()
    overlap = sum((Counter(predicted_tokens) & Counter(expected_tokens)).values())
    return _overlap_metrics(overlap, len(predicted_tokens), len(expected_tokens),
                            predicted == expected)


def support_metrics(predicted: Iterable, reference: Iterable) -> dict[str, float]:
    """Official exact-coordinate set scoring; repeated citations earn no credit."""
    predicted_set = {_coordinate(value) for value in predicted}
    reference_set = {_coordinate(value) for value in reference}
    return _overlap_metrics(len(predicted_set & reference_set), len(predicted_set),
                            len(reference_set), predicted_set == reference_set)


def _invalid_output(reason: str) -> dict:
    return {**dict.fromkeys(OFFICIAL_METRIC_NAMES, 0.0),
            "valid_output": False, "invalid_reason": reason}


def score_prediction(prediction: Any, view: Mapping, gold: Mapping) -> dict:
    """Map citations to source coordinates and calculate official joint metrics.

    Dataset/view/label errors raise ValueError. Malformed model output instead
    receives zero for *all* metrics, explicitly flagged; it is never repaired or
    partially credited. An empty answer or citation list is structurally valid.
    """
    lookup = _unit_lookup(view)
    gold = _validated_gold(gold, lookup)
    if not isinstance(prediction, Mapping):
        return _invalid_output("prediction must be an object")
    if set(prediction) != {"answer", "citations"}:
        return _invalid_output("prediction must contain exactly answer/citations")
    if not isinstance(prediction["answer"], str):
        return _invalid_output("answer must be a string")
    citations = prediction["citations"]
    if not isinstance(citations, list):
        return _invalid_output("citations must be a list")
    for citation in citations:
        if not isinstance(citation, str) or citation not in lookup:
            return _invalid_output("citation must be a known unit id")
    answer = answer_metrics(prediction["answer"], gold["answer"])
    support = support_metrics([lookup[citation] for citation in citations],
                              gold["supporting_facts"])
    joint_precision = answer["prec"] * support["prec"]
    joint_recall = answer["recall"] * support["recall"]
    denominator = joint_precision + joint_recall
    joint = {
        "em": answer["em"] * support["em"],
        "f1": 2 * joint_precision * joint_recall / denominator if denominator else 0.0,
        "prec": joint_precision,
        "recall": joint_recall,
    }
    return {**answer, **{"sp_" + key: value for key, value in support.items()},
            **{"joint_" + key: value for key, value in joint.items()},
            "valid_output": True, "invalid_reason": None}
