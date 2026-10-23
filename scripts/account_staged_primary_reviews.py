"""Offline import of two separately saved review copies. Private outputs only.

Requires pinned input digests. Empty slots, disagreements and inadequate
disclosures remain unknown; this tool never creates an adjudication decision.
"""
import argparse
import hashlib
from pathlib import Path
from runtime import cache_intent_review_accounting as accounting
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_entry import pinned_bytes
from runtime.cache_intent_journal import Journal
from scripts.prepare_staged_contract_review import load_archive
from scripts.fetch_bird_fixed import absent,strict_json


def run(packet,*,packet_sha,review_inputs,output):
    packet=Path(packet).absolute(); output=Path(output).absolute()
    if output==packet or output.is_relative_to(packet): raise ValueError("Preserve original blank packet")
    absent(output)
    if type(review_inputs) is not list or len(review_inputs)!=2: raise ValueError("Two review slots required")
    _,manifest=load_archive(packet,packet_sha)
    def read(name): return strict_json(pinned_bytes(packet/name,manifest[name]).decode("utf-8"))
    template=read("reviewer-1.json")
    if template!=read("reviewer-2.json"): raise ValueError("Different original review templates")
    if (any(v is not None for v in template["reviewer"].values())
            or any(v is not None for i in template["items"] for v in i["review"].values())):
        raise ValueError("Pinned template must be the unchanged blank export")
    reviews=[]; input_refs=[]
    for item in review_inputs:
        if item is None: reviews.append(None); input_refs.append(None); continue
        path,sha=item
        raw=pinned_bytes(Path(path),sha)
        reviews.append(strict_json(raw.decode("utf-8")))
        input_refs.append(dict(file_sha256=sha,bytes=len(raw)))
    result=accounting.summarize(template,read("organizer-only-mapping.json"),reviews)
    journal=Journal(output)
    journal("input-provenance",dict(packet_manifest_sha256=packet_sha,review_files=input_refs))
    journal("supplied-reviews",dict(reviews=reviews))
    journal("provisional-accounting",result)
    for label,path in (("importer",Path(__file__)),("accounting",Path(accounting.__file__))):
        raw=path.read_bytes()
        journal(label+"-source",dict(file_sha256=hashlib.sha256(raw).hexdigest(),source_utf8=raw.decode("utf-8")))
    journal.seal()
    # No private quotes or names printed to the terminal.
    return dict(status=result["status"],planned_slots=12,independent_targets=2,
        supplied_review_files=sum(r is not None for r in reviews),
        known_provisional_labels=sum(r["provisional_contradiction"] is not None for r in result["rows"]),
        adjudication_completed=False,model_calls=0,network_requests=0)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("packet","packet-sha","output"): p.add_argument("--"+name,required=True)
    for n in (1,2): p.add_argument(f"--review-{n}",nargs=2,metavar=("PATH","SHA256"))
    a=p.parse_args()
    result=run(a.packet,packet_sha=a.packet_sha,review_inputs=[a.review_1,a.review_2],output=a.output)
    print(audit.encoded(result).decode())


if __name__=="__main__": main()
