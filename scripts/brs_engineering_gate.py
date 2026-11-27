"""Structural release evidence gate; does not authenticate reviewer identities."""
import argparse
import json
import re

def blockers(record):
    sha = record.get("candidate_sha")
    errors = []
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        errors.append("invalid candidate SHA")
    author = record.get("builder")
    if not isinstance(author, str) or not author.strip():
        errors.append("missing builder")
    for role in ("tester", "audit", "architect"):
        evidence = record.get(role)
        if not isinstance(evidence, dict):
            errors.append("missing " + role)
            continue
        identity = evidence.get("identity")
        if not isinstance(identity, str) or not identity.strip() or identity == author:
            errors.append("missing independent " + role)
        if evidence.get("sha") != sha or evidence.get("passed") is not True:
            errors.append("stale or failed " + role)
        reference = evidence.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            errors.append("missing evidence reference " + role)
    if record.get("unresolved_blockers") != []:
        errors.append("unresolved or unreported blockers")
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record")
    args = parser.parse_args()
    with open(args.record, encoding="utf-8") as f:
        errors = blockers(json.load(f))
    print(json.dumps({"structural_pass": not errors, "blockers": errors,
                      "release_authority": False}))
    return int(bool(errors))

if __name__ == "__main__":
    raise SystemExit(main())
