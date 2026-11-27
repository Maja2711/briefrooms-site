"""Read-only independent verification of bounded newsroom repair PRs.

Never executes candidate code on the host or grants release authority.
Uses trusted controller code from the main-owned checkout.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.request import Request, build_opener, HTTPRedirectHandler

from brs_self_healing import MODULES, REPO, SHA_RE, name_for, test_path, sandbox, suite

API = "https://api.github.com/repos/" + REPO
BRANCH = re.compile(r"brs/self-heal/news-([0-9a-f]{16})")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api(path):
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("read-only GH_TOKEN missing")
    request = Request(API + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "BRs-Independent-Verifier"})
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        body = response.read(8_000_001)
    if len(body) > 8_000_000:
        raise ValueError("API response too large")
    return json.loads(body)


def pages(path, get=api):
    result = []
    for page in range(1, 11):
        rows = get(path + ("&" if "?" in path else "?") + "per_page=100&page=" + str(page))
        if not isinstance(rows, list):
            raise ValueError("invalid API list")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ValueError("API pagination limit")


def identity(pr):
    head = pr["head"]
    match = BRANCH.fullmatch(head["ref"])
    if (not match or head["repo"]["full_name"] != REPO or
            pr["base"]["repo"]["full_name"] != REPO or pr["base"]["ref"] != "main" or
            pr["state"] != "open" or pr.get("merged") or
            not SHA_RE.fullmatch(head["sha"]) or not SHA_RE.fullmatch(pr["base"]["sha"])):
        raise ValueError("not an open in-scope repair PR")
    return match.group(1), head["sha"], pr["base"]["sha"]


def validate_scope(files, fp):
    names = [f["filename"] for f in files]
    regression = test_path(fp)
    if (len(set(names)) != len(names) or not 2 <= len(names) <= len(MODULES)+1 or
            regression not in names or set(names)-set(MODULES)-{regression}):
        raise ValueError("out-of-scope PR diff")
    for f in files:
        expected = "added" if f["filename"] == regression else "modified"
        if f["status"] != expected or f.get("previous_filename"):
            raise ValueError("unexpected file operation")
    return sorted(names)


def git(*args):
    # Public repository, no token, no credential helper.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_TERMINAL_PROMPT="0")
    return subprocess.check_output(["git", "-c", "credential.helper=", *args],
                                   env=env, timeout=180, stderr=subprocess.DEVNULL)


def export(sha, directory, temp):
    if not SHA_RE.fullmatch(sha):
        raise ValueError("invalid commit SHA")
    directory.mkdir(mode=0o755)
    archive = temp / (directory.name + ".tar")
    git("archive", "--format=tar", "-o", str(archive), sha)
    with tarfile.open(archive) as tf:
        tf.extractall(directory, filter="data")
    archive.unlink()


def independent_tests(head, parent, fp, paths):
    # Reject nonregular files, mode changes, renames, and any hidden Git diff.
    raw = git("diff", "--raw", "--no-renames", parent, head).decode()
    actual = []
    for line in raw.splitlines():
        meta, path = line.split("\t", 1)
        oldmode, newmode, _, _, action = meta[1:].split()
        if path == test_path(fp):
            if (oldmode, newmode, action) != ("000000", "100644", "A"):
                raise ValueError("invalid regression file mode")
        elif (oldmode, newmode, action) != ("100644", "100644", "M"):
            raise ValueError("invalid source file mode")
        actual.append(path)
    if sorted(actual) != paths:
        raise ValueError("Git diff differs from API scope")
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        temp.chmod(0o755)
        base, candidate = temp/"base", temp/"candidate"
        export(parent, base, temp)
        export(head, candidate, temp)
        test = candidate/test_path(fp)
        if not test.is_file() or test.is_symlink() or test.stat().st_size > 12000:
            raise ValueError("invalid regression")
        target = base/test_path(fp)
        if target.exists() or target.is_symlink() or target.parent.resolve() != target.parent.absolute():
            raise ValueError("regression already present or unsafe")
        target.write_bytes(test.read_bytes())
        red = sandbox(base, ("tests.test_brs_heal_" + fp,))
        # Do not publish raw candidate output; only a narrowly defined verdict.
        reproduced = bool(red.returncode and re.search(r"^FAIL: test_", red.stdout, re.M)
                          and not re.search(r"^ERROR:", red.stdout, re.M))
        if not reproduced:
            return {"red": False, "green": False}
        green = sandbox(candidate, suite(candidate))
        return {"red": True, "green": green.returncode == 0 and
                bool(re.search(r"Ran [1-9]\d* tests?", green.stdout))}


def verify(number, get=api, test=independent_tests, fetch=git):
    if type(number) is not int or number < 1:
        raise ValueError("invalid PR number")
    endpoint = "/pulls/" + str(number)
    before = get(endpoint)
    fp, head, base = identity(before)
    paths = validate_scope(pages(endpoint+"/files", get), fp)
    commits = pages(endpoint+"/commits", get)
    if len(commits) != 1 or commits[0]["sha"] != head or len(commits[0]["parents"]) != 1:
        raise ValueError("repair PR must be a single-parent single commit")
    parent = commits[0]["parents"][0]["sha"]
    if not SHA_RE.fullmatch(parent):
        raise ValueError("invalid parent")
    fetch("fetch", "--no-tags", "--depth=2", "https://github.com/"+REPO+".git", head)
    actual = fetch("rev-list", "--parents", "-n", "1", head).decode().strip().split()
    if actual != [head, parent]:
        raise ValueError("parent mismatch")
    verdict = test(head, parent, fp, paths)
    after = get(endpoint)
    same = identity(after) == (fp, head, base)
    return {"pr": number, "head_sha": head, "parent_sha": parent, "observed_main_sha": base,
            "scope": paths, "status": ("VERIFIED_CANDIDATE" if same and verdict["red"] is True and verdict["green"] is True
                                      else "STALE" if not same else "FAILED"),
            "red": verdict["red"], "green": verdict["green"],
            "mergeable": after.get("mergeable"), "release_authority": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--pr", type=int, default=0)
    args = p.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != REPO or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise SystemExit("independent verifier is main-owned")
    if args.pr:
        numbers = [args.pr]
    else:
        numbers = sorted(x["number"] for x in pages("/pulls?state=open&base=main")
                         if BRANCH.fullmatch(x["head"]["ref"]) and (x["head"].get("repo") or {}).get("full_name")==REPO)
        # One candidate per cycle, rotate so a persistent incident cannot starve others.
        if numbers:
            numbers = [numbers[int(datetime.now(timezone.utc).timestamp()//3600) % len(numbers)]]
    results = []
    for number in numbers:
        try:
            results.append(verify(number))
        except Exception as exc:
            results.append({"pr":number,"status":"ERROR","reason":type(exc).__name__,"release_authority":False})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"schema":"brs-independent-verification-v1","results":results,
                                      "checked_at":datetime.now(timezone.utc).isoformat(),
                                      "release_authority":False},indent=2))
    return int(any(x["status"] != "VERIFIED_CANDIDATE" for x in results))


if __name__ == "__main__":
    raise SystemExit(main())
