#!/usr/bin/env python3
"""Independent real-time EURUSD Daily evidence recorder (SHADOW ONLY).

Run beside, NEVER inside, the five-second execution critical path. It does
not edit or push canonical spot/history. The only REST write target is
data/investments/eurusd_exit_signal_journal.json.

On every market poll, record the exact FIRST-SEEN wall-clock time for each
already closed 1m bar. Retroactively observed candles are explicitly LATE,
not represented as signals available at their bar close. Spool first, then
merge via GitHub Contents API using conditional SHA; failures preserve spool.

The scheduled five-minute retrospective observer remains useful for reviews,
but is NOT a reliable primary clock for open-position signal evidence.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from daily_eurusd_exit_intelligence_v2 import (
    MAX_SNAPSHOTS, capture, iso, load, normalize_bars, parse_time, save,
)

REPO = "Maja2711/briefrooms-site"
JOURNAL = "data/investments/eurusd_exit_signal_journal.json"
SCHEMA = "eurusd-realtime-1m-evidence-spool-v1"
POLL_SECONDS = 60
FLUSH_SECONDS = 180
TIMELY_SECONDS = 180
MAX_CATCHUP_BARS = 180
MAX_WAIT_FOR_BAR_SECONDS = 240
MAX_HEARTBEATS = 50
STOP = False


def _stop(_signal: int, _frame: Any) -> None:
    global STOP
    STOP = True


def _position(spot: Mapping[str, Any]) -> Mapping[str, Any] | None:
    p = (spot.get("metadata") or {}).get("position")
    return p if isinstance(p, Mapping) and p.get("status") == "OPEN" and p.get("trade_id") else None


def _unique_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("trade_id") or ""), str(row.get("market_bar_at") or "")


def merge_snapshots(existing: Sequence[Mapping[str, Any]],
                    arriving: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Earliest actual first-seen evidence wins, not retrospective reconstruction.

    If another research writer later observed the same historical bar, it
    cannot overwrite or backdate the first-seen timestamp recorded live.
    """
    grouped: dict[tuple[str, str], dict] = {}
    for row in list(existing) + list(arriving):
        if not isinstance(row, Mapping):
            continue
        key = _unique_key(row)
        if not all(key):
            continue
        previous = grouped.get(key)
        ts = parse_time(row.get("first_seen_at") or row.get("captured_at"))
        prev_ts = parse_time(previous.get("first_seen_at") or previous.get("captured_at")) if previous else None
        if ts is None:
            continue
        if previous is None or prev_ts is None or ts < prev_ts:
            grouped[key] = dict(row)
    return sorted(grouped.values(), key=lambda x: (x.get("market_bar_at",""),x.get("trade_id","")))[-MAX_SNAPSHOTS:]


def collect(spool: Mapping[str, Any], spot: Mapping[str, Any],
            raw_bars: Sequence[Any], now: datetime) -> dict:
    """Pure snapshot capture. No clock is invented and no market future is used."""
    out = copy.deepcopy(dict(spool))
    out.setdefault("schema_version", SCHEMA)
    out.setdefault("snapshots", [])
    out.setdefault("heartbeats", [])
    position = _position(spot)
    if not position:
        # Absence of a position is NOT a feed failure or reason to erase spool.
        out["monitor_health"] = {
            "status":"NO_OPEN_POSITION", "sampled_at":iso(now),
            "exit_authority":False,
        }
        return out
    tid = str(position["trade_id"])
    opened = parse_time(position.get("opened_at"))
    if opened is None:
        out["monitor_health"] = {
            "status":"INVALID_OPEN_TIME","active_trade_id":tid,
            "sampled_at":iso(now), "exit_authority":False,
        }
        return out
    bars = normalize_bars(raw_bars, now)
    complete = [b for b in bars if b["time"] >= opened and
                b["time"] + timedelta(minutes=1) <= now]
    last_known = max((b["time"] for b in complete),default=None)
    delay = (now - (last_known+timedelta(minutes=1))).total_seconds() if last_known else None

    keys = {_unique_key(s) for s in out["snapshots"]}
    new_rows = []
    for bar in complete[-MAX_CATCHUP_BARS:]:
        key = tid, iso(bar["time"])
        if key in keys:
            continue
        asof = bar["time"] + timedelta(minutes=1)
        # Historical closed-candle reconstruction uses bar-close as
        # features' as-of, but FIRST_SEEN is the actual collection instant.
        history = [b for b in bars if b["time"] <= bar["time"]]
        sample = capture(position, history, [], asof)
        if not sample:
            continue
        age = max(0.0, (now-asof).total_seconds())
        sample.update({
            "captured_at":iso(now), "first_seen_at":iso(now),
            "market_bar_closed_at":iso(asof),
            "availability_latency_seconds":round(age,2),
            "timely_observation":age <= TIMELY_SECONDS,
            "mode":("TIMELY_ASOF_OBSERVATION" if age <= TIMELY_SECONDS
                    else "DELAYED_BATCH_RECONSTRUCTION"),
            "retrospective_bar_not_a_live_alert":age > TIMELY_SECONDS,
            "collector":"INDEPENDENT_REALTIME_WATCHER_1M",
            "source_quality":"INDICATIVE_YAHOO_1M_NOT_EXECUTABLE",
            "market_data_10y_not_fetched":True,
            "exit_authority":False,
        })
        keys.add(key)
        new_rows.append(sample)
    out["snapshots"] = merge_snapshots(out["snapshots"], new_rows)

    active = [s for s in out["snapshots"] if s["trade_id"] == tid]
    timely = [s for s in active if s.get("timely_observation") is True]
    last_timely = max((s.get("market_bar_at") for s in timely),default=None)
    chron = sorted({s["market_bar_at"] for s in timely})
    late_gaps = sum(
        (parse_time(chron[i])-parse_time(chron[i-1])).total_seconds() > 61
        for i in range(1,len(chron))
    )
    first_full_minute = opened.replace(second=0,microsecond=0)
    if first_full_minute < opened:
        first_full_minute += timedelta(minutes=1)
    expected = max(0,int((last_known-first_full_minute).total_seconds()/60)+1) if last_known else 0
    seen_from_open = sum((parse_time(t) or opened-timedelta(days=1)) >= first_full_minute
                         for t in chron)
    missing = max(0, expected-seen_from_open)
    coverage = round(seen_from_open/expected,4) if expected else None
    status = ("OK" if delay is not None and 0 <= delay <= MAX_WAIT_FOR_BAR_SECONDS
              and timely else "STALE_OR_MISSING_FX_1M_BAR"
              if delay is None or delay > MAX_WAIT_FOR_BAR_SECONDS else
              "NO_TIMELY_1M_OBSERVATIONS")
    health = {
        "status":status,"sampled_at":iso(now),
        "active_trade_id":tid,"latest_source_bar_at":iso(last_known) if last_known else None,
        "latest_source_closed_at":iso(last_known+timedelta(minutes=1)) if last_known else None,
        "source_age_after_close_seconds":round(delay,2) if delay is not None else None,
        "new_snapshots":len(new_rows), "total_trade_snapshots":len(active),
        "timely_trade_snapshots":len(timely),
        "late_trade_snapshots":len(active)-len(timely),
        "last_timely_bar_at":last_timely,
        "detected_gaps_among_timely_bars":late_gaps,
        "expected_full_minutes_since_entry":expected,
        "timely_1m_minutes_from_entry":seen_from_open,
        "missing_or_late_minutes_since_entry":missing,
        "timely_lifetime_coverage_ratio":coverage,
        "whole_open_lifetime_continuity_proven":bool(expected and not missing),
        "publication_pending":True,
        "exit_authority":False,
    }
    out["monitor_health"] = health
    beat = {
        "at":iso(now),"trade_id":tid,"status":status,
        "latest_source_closed_at":health["latest_source_closed_at"],
        "new_snapshots":len(new_rows),
    }
    out["heartbeats"] = (list(out["heartbeats"])+[beat])[-MAX_HEARTBEATS:]
    return out


def merge_journal(remote: Mapping[str, Any], spool: Mapping[str, Any],
                  now: datetime) -> dict:
    """Preserve all other research sections and append ONLY EURUSD snapshots."""
    if remote and remote.get("authority") not in (None,"SHADOW_OBSERVATION_ONLY"):
        raise ValueError("journal has unexpected authority")
    output = copy.deepcopy(dict(remote))
    incoming = spool.get("snapshots") or []
    output["snapshots"] = merge_snapshots(output.get("snapshots") or [],incoming)
    output["schema_version"] = output.get("schema_version","eurusd-exit-intelligence-v2")
    output["authority"] = "SHADOW_OBSERVATION_ONLY"
    health = dict(spool.get("monitor_health") or {})
    health["publication_pending"] = False
    health["last_published_at"] = iso(now)
    output["realtime_monitor_health"] = health
    output["realtime_heartbeats"] = list(spool.get("heartbeats") or [])[-MAX_HEARTBEATS:]
    output["generated_at"] = iso(now)
    return output


def api_request(url: str, token: str, method: str = "GET",
                payload: Mapping[str, Any] | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization":"Bearer "+token,
                 "Accept":"application/vnd.github+json",
                 "X-GitHub-Api-Version":"2022-11-28",
                 "User-Agent":"BriefRooms-Daily-EURUSD-Shadow-Evidence/1.0",
                 "Content-Type":"application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        body = response.read()
    return json.loads(body) if body else {}


def publish(spool: Mapping[str, Any], token: str, repository: str,
            now: datetime, request=api_request) -> dict:
    """CAS on exact GitHub blob SHA. Retries must RE-MERGE with latest remote."""
    if repository != REPO:
        raise ValueError("unexpected GitHub repository for EURUSD journal")
    if not token:
        raise ValueError("missing GitHub token")
    url = f"https://api.github.com/repos/{REPO}/contents/{JOURNAL}"
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            doc = request(url,token)
            if doc.get("type") != "file" or not doc.get("sha"):
                raise RuntimeError("missing journal SHA; refuse overwrite")
            body = base64.b64decode(doc["content"].replace("\n",""))
            remote = json.loads(body)
            updated = merge_journal(remote,spool,now)
            if updated == remote:
                return {"status":"UNCHANGED","snapshot_count":len(updated["snapshots"])}
            payload = {
                "message":"data(daily): persist live EURUSD first-seen 1m evidence",
                "branch":"main",
                "sha":doc["sha"],
                "content":base64.b64encode(
                    (json.dumps(updated,indent=2,ensure_ascii=False,sort_keys=True)+"\n").encode()
                ).decode("ascii"),
            }
            result = request(url,token,"PUT",payload)
            if not (result.get("content") or {}).get("sha"):
                raise RuntimeError("journal update returned no content SHA")
            return {"status":"PUBLISHED","snapshot_count":len(updated["snapshots"]),
                    "commit_sha":(result.get("commit") or {}).get("sha")}
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (409,422):
                raise
        if attempt < 5:
            time.sleep(min(2+attempt,7))
    raise RuntimeError("unable to CAS-persist evidence after retries") from last_error


ARCHIVE_PREFIX = "data/investments/eurusd_live_signal_archive"


def archive_path(row: Mapping[str, Any]) -> str:
    """Fixed UTC two-hour slot: <=120 distinct 1m observations per shard."""
    point = parse_time(row.get("market_bar_at"))
    if point is None:
        raise ValueError("archive bar timestamp missing")
    hour = 2 * (point.hour // 2)
    return f"{ARCHIVE_PREFIX}/{point:%Y%m%d}/{hour:02d}.json"


def archive_shards(spool: Mapping[str, Any]) -> dict[str,list[dict]]:
    groups: dict[str,list[dict]] = {}
    for row in spool.get("snapshots") or []:
        if not isinstance(row,Mapping) or not row.get("first_seen_at"):
            continue
        groups.setdefault(archive_path(row),[]).append(dict(row))
    return groups


def publish_archives(spool: Mapping[str, Any], token: str, repository: str,
                     now: datetime, request=api_request) -> dict:
    """Persist archives BEFORE compacting hot journal; CAS-merge each shard."""
    if repository != REPO:
        raise ValueError("unexpected GitHub repository")
    if not token:
        raise ValueError("missing GitHub token")
    completed=0
    for path, rows in sorted(archive_shards(spool).items()):
        url=f"https://api.github.com/repos/{REPO}/contents/{path}"
        success=False
        for attempt in range(6):
            try:
                try:
                    doc=request(url,token)
                    if doc.get("type") != "file" or not doc.get("sha"):
                        raise RuntimeError("archive content SHA missing")
                    base=json.loads(base64.b64decode(doc["content"].replace("\n","")))
                    sha=doc["sha"]
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                    base={}
                    sha=None
                if base and base.get("authority") != "SHADOW_OBSERVATION_ONLY":
                    raise ValueError("unexpected archive authority")
                old=list(base.get("snapshots") or [])
                # Two-hour buckets have at most 120 EURUSD 1m timestamps.
                merged=merge_snapshots(old,rows)
                if len(merged)>120:
                    raise ValueError("archive bucket contains >120 distinct 1m bars")
                if old==merged:
                    success=True
                    break
                value={
                    "schema_version":"eurusd-first-seen-signal-archive-v1",
                    "authority":"SHADOW_OBSERVATION_ONLY",
                    "bucket_utc":path.removeprefix(ARCHIVE_PREFIX+"/"),
                    "snapshots":merged,
                    "generated_at":iso(now),
                    "append_only_provenance":True,
                }
                put={
                    "branch":"main",
                    "message":"data(daily): archive EURUSD first-seen live 1m evidence",
                    "content":base64.b64encode(
                        (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
                    ).decode(),
                }
                if sha:
                    put["sha"]=sha
                response=request(url,token,"PUT",put)
                if not (response.get("content") or {}).get("sha"):
                    raise RuntimeError("archive write confirmation missing")
                success=True
                break
            except urllib.error.HTTPError as error:
                if error.code not in (409,422):
                    raise
                if attempt < 5:
                    time.sleep(min(2+attempt,7))
        if not success:
            raise RuntimeError("unable to CAS-archive EURUSD evidence at "+path)
        completed+=1
    return {"status":"ARCHIVED","shards_verified":completed}


def publish_all(spool: Mapping[str, Any], token: str, repository: str,
                now: datetime, request=api_request) -> dict:
    archival=publish_archives(spool,token,repository,now,request=request)
    hot=publish(spool,token,repository,now,request=request)
    return {"archive":archival,"hot":hot}


def _spool(path: Path) -> dict:
    return load(path,{"schema_version":SCHEMA,"snapshots":[],"heartbeats":[]})


def watch(path: Path,spot_path: Path,token: str,repository: str,
          poll_seconds: int = POLL_SECONDS,
          flush_seconds: int = FLUSH_SECONDS) -> int:
    """Dedicated subprocess; MUST NOT wait on or delay the 5s trade trigger."""
    last_flush = 0.0
    saw_open = False
    try:
        from belief_market_data_adapter import YahooChartClient
        client = YahooChartClient(timeout=10)
    except Exception as exc:
        print("EVIDENCE_CLIENT_UNAVAILABLE",type(exc).__name__,flush=True)
        return 2
    while not STOP:
        started=time.monotonic()
        now=datetime.now(timezone.utc)
        spot=load(spot_path,{})
        position=_position(spot)
        if position:
            saw_open=True
            try:
                bars=client.bars("EURUSD=X","5d","1m")
                updated=collect(_spool(path),spot,bars,now)
                save(path,updated)  # spool BEFORE any remote network attempt
                health=updated["monitor_health"]
                print("EURUSD_LIVE_EVIDENCE",json.dumps({
                    "trade":health.get("active_trade_id"),
                    "status":health["status"],
                    "new":health.get("new_snapshots"),
                    "timely":health.get("timely_trade_snapshots"),
                    "late":health.get("late_trade_snapshots")}),flush=True)
                if health["status"] != "OK":
                    print("::warning::EURUSD evidence feed degraded: "+health["status"],flush=True)
            except Exception as exc:
                pending=_spool(path)
                pending["monitor_health"]={
                    "status":"SOURCE_OR_CAPTURE_ERROR","sampled_at":iso(now),
                    "error_type":type(exc).__name__,"active_trade_id":position.get("trade_id"),
                    "publication_pending":True,"exit_authority":False}
                save(path,pending)
                print("::warning::EURUSD live evidence capture error: "+
                      type(exc).__name__,flush=True)
        elif saw_open:
            break
        else:
            # The caller exits on NO_OPEN; don't keep a stale observer alive.
            break
        if time.monotonic()-last_flush>=flush_seconds:
            try:
                result=publish_all(_spool(path),token,repository,datetime.now(timezone.utc))
                last_flush=time.monotonic()
                print("EURUSD_EVIDENCE_PUBLISH",result,flush=True)
            except Exception as exc:
                print("::warning::EURUSD evidence pending local spool: "+
                      type(exc).__name__,flush=True)
        left=max(0.0,poll_seconds-(time.monotonic()-started))
        until=time.monotonic()+left
        while not STOP and time.monotonic()<until:
            time.sleep(min(1,until-time.monotonic()))
    # SIGTERM, transition to CLOSED, or natural end: final CAS merge.
    if path.exists():
        try:
            print("EURUSD_EVIDENCE_FINAL_PUBLISH",
                  publish_all(_spool(path),token,repository,datetime.now(timezone.utc)),
                  flush=True)
        except Exception as exc:
            print("::warning::EURUSD evidence final flush failed; spool preserved: "+
                  type(exc).__name__,flush=True)
            return 2
    return 0


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--spool",required=True)
    parser.add_argument("--spot",default="data/investments/eurusd_daily_spot.json")
    parser.add_argument("--watch",action="store_true")
    parser.add_argument("--flush",action="store_true")
    args=parser.parse_args()
    if args.watch == args.flush:
        parser.error("choose exactly one of --watch or --flush")
    path=Path(args.spool)
    token=os.getenv("GH_TOKEN","")
    repository=os.getenv("GITHUB_REPOSITORY","")
    if args.flush:
        if not path.exists():
            return 0
        print(publish_all(_spool(path),token,repository,datetime.now(timezone.utc)))
        return 0
    signal.signal(signal.SIGTERM,_stop)
    signal.signal(signal.SIGINT,_stop)
    return watch(path,Path(args.spot),token,repository)


if __name__=="__main__":
    sys.exit(main())
