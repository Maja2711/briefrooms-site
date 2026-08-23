from __future__ import annotations
import json,sys,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"; sys.path.insert(0,str(SCRIPTS))
import gse_discovery_common as c
import gse_discovery_sources as s
import gse_discovery_cluster as k
import gse_historical_event_discovery as d
UTC=timezone.utc

class FakeClient:
    def __init__(self,pages): self.pages=pages; self.calls=[]
    def get_text(self,url): self.calls.append(url); return self.pages[url]
def detail(title,date,body="Iran sanctions designation"): return f'<html><h1>{title}</h1><time datetime="{date}">{date}</time><p>{body}</p></html>'

class Tests(unittest.TestCase):
    def test_official_and_classifier(self):
        self.assertTrue(c.host_is_official("https://ofac.treasury.gov/recent-actions/x")); self.assertFalse(c.host_is_official("https://example.com/x")); self.assertEqual(c.classify_scenarios("Russia-related Designations Removals"),[]); names={x["scenario_type"] for x in c.classify_scenarios("Russia-related Designations")}; self.assertIn("sanctions_escalation",names); self.assertIn("russia_ukraine_black_sea_escalation",names)
    def test_verify_primary_document(self):
        url="https://ofac.treasury.gov/recent-actions/2024-01-10-russia-related-designations"; candidate={"candidate_id":"c1","source_name":"OFAC","source_kind":"ofac_recent_actions","source_ref":url,"source_reliability":.98,"list_title":"Russia-related Designations","list_date":"2024-01-10T00:00:00Z","preclassification":c.classify_scenarios("Russia-related Designations"),"discovery_verified":False}; row=s.verify_document(candidate,FakeClient({url:detail("Russia-related Designations","2024-01-10T12:00:00Z","Treasury sanctions Russian entities over Ukraine aggression")}),start_at=datetime(2014,1,1,tzinfo=UTC),end_at=datetime(2026,1,1,tzinfo=UTC)); self.assertIsNotNone(row); self.assertEqual(row["verification_status"],"machine_verified_primary_source"); self.assertFalse(row["market_outcomes_used"])
    def test_cluster_correlation_cap(self):
        def row(i,day): return {"candidate_id":f"c{i}","source_name":"OFAC","source_ref":f"https://ofac.treasury.gov/recent-actions/{i}","source_reliability":.98,"title":"Russia-related Designations targeting Ukraine aggression","event_at":f"2024-01-{day:02d}T00:00:00Z","scenario_scores":[{"scenario_type":"sanctions_escalation","score":.9},{"scenario_type":"russia_ukraine_black_sea_escalation","score":.8}],"scenario_types":["sanctions_escalation","russia_ukraine_black_sea_escalation"],"actor_buckets":["russia_ukraine"],"features":{x:.5 for x in c.FEATURE_KEYS},"verification_score":.95,"verification_status":"machine_verified_primary_source","discovery_verified":True}
        clusters=k.cluster_verified_documents([row(1,10),row(2,12),row(3,25)]); self.assertEqual(len(clusters),2); self.assertEqual(clusters[0]["supporting_document_count"],2)
    def test_target_gate_100(self):
        clusters=[]
        for i in range(100): clusters.append({"cluster_id":f"c{i}","event_cluster_id":f"c{i}","event_at":f"2020-{(i%12)+1:02d}-{(i%27)+1:02d}T00:00:00Z","last_event_at":"2020-01-01T00:00:00Z","label":f"Iran sanctions {i}","scenario_types":["sanctions_escalation"],"dominant_scenario":"sanctions_escalation","dominant_actor":"iran","source":"OFAC","source_ref":f"https://ofac.treasury.gov/recent-actions/{i}","source_refs":[f"https://ofac.treasury.gov/recent-actions/{i}"],"source_reliability":.98,"features":{x:.5 for x in c.FEATURE_KEYS},"supporting_document_count":1,"distinct_source_count":1,"verification_score":.9,"verification_status":"machine_verified_primary_source_cluster","document_ids":[f"d{i}"],"market_outcomes_used_for_selection":False})
        merged=k.merge_catalog({"catalog_version":"base","events":[]},clusters,generated_at=datetime(2026,1,1,tzinfo=UTC)); self.assertTrue(merged["coverage"]["target_met"]); self.assertGreaterEqual(merged["coverage"]["verified_cluster_n"],100)
    def test_listing_and_full_run(self):
        list_url="https://ofac.treasury.gov/recent-actions/sanctions-list-updates?page=0"; detail_url="https://ofac.treasury.gov/recent-actions/2024-01-10-russia-related-designations"; listing=f'<a href="{detail_url}">Russia-related Designations</a> January 10, 2024 - Sanctions List Updates'; source={"name":"OFAC","kind":"ofac_recent_actions","source_reliability":.98,"list_url":"https://ofac.treasury.gov/recent-actions/sanctions-list-updates?page={page}","max_pages":1,"empty_page_stop":1}; rows,diag=s.discover_source_documents(source,FakeClient({list_url:listing}),start_at=datetime(2014,1,1,tzinfo=UTC),end_at=datetime(2026,1,1,tzinfo=UTC)); self.assertEqual(len(rows),1)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); curated=root/"curated.json"; cfg=root/"cfg.json"; curated.write_text(json.dumps({"catalog_version":"base","events":[{"event_id":"seed","event_cluster_id":"seed","event_at":"2020-01-01T00:00:00Z","label":"seed","scenario_types":["sanctions_escalation"],"source":"Treasury","source_ref":"https://home.treasury.gov/seed","source_reliability":.9,"features":{x:.5 for x in c.FEATURE_KEYS}}]})); cfg.write_text(json.dumps({"start_date":"2014-01-01T00:00:00Z","sources":[source]})); client=FakeClient({list_url:listing,detail_url:detail("Russia-related Designations","2024-01-10T00:00:00Z","Treasury sanctions Russian entities over Ukraine aggression")}); state=d.run(root,curated,cfg,now=datetime(2025,1,1,tzinfo=UTC),force_full=True,client=client); self.assertTrue((root/"gse_historical_event_catalog_effective.json").exists()); self.assertFalse(state["controls"]["market_outcomes_used_for_event_selection"])

if __name__=="__main__": unittest.main()
