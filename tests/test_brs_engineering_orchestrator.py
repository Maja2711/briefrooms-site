import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from brs_engineering_graph import Job
from brs_engineering_queue import enqueue,claim,finish,status
from brs_engineering_dispatcher import snapshot,tick,REPO,verify_candidate
from brs_engineering_executor import ROLES,expected

SHA="a"*40

class OrchestratorTests(unittest.TestCase):
    def test_fencing_restart_and_dependency_blocking(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"jobs.db"
            jobs=(Job("a","github"),Job("b","search",("a",)),Job("c","grep"))
            self.assertTrue(enqueue(db,"r",1128,SHA,jobs))
            self.assertFalse(enqueue(db,"r",1128,SHA,jobs))
            with self.assertRaises(ValueError):
                enqueue(db,"r",1129,SHA,jobs)
            with patch("brs_engineering_queue.time.time",return_value=100):
                a=claim(db,"r",10)
                c=claim(db,"r",100)
                self.assertEqual((a["id"],c["id"]),("a","c"))
                self.assertIsNone(claim(db,"r"))
                finish(db,c,{"status":"PASS"})
            with patch("brs_engineering_queue.time.time",return_value=111):
                self.assertIsNone(claim(db,"r"))
                with self.assertRaises(ValueError):
                    finish(db,a,{"status":"PASS"})
            self.assertEqual({x["id"]:x["state"] for x in status(db,"r")},
                             {"a":"ESCALATED","b":"BLOCKED","c":"DONE"})

    def test_snapshot_is_commit_bound(self):
        pull={"head":{"sha":SHA,"repo":{"full_name":REPO}},"base":{"sha":"b"*40},
              "state":"open","merged":False,"mergeable":True}
        def get(path, token):
            self.assertEqual(token,"test-token")
            if path.startswith("/pulls/"):
                return pull
            return {"check_runs":[{"id":1,"head_sha":SHA,"name":"CI","status":"completed","conclusion":"success"}]}
        r=snapshot(1128,SHA,"test-token",get)
        self.assertEqual(r["checks"][0]["conclusion"],"success")
        self.assertFalse(r["required_checks_verified"])
        self.assertFalse(r["release_authority"])
        with self.assertRaises(ValueError):
            snapshot(1128,"c"*40,"test-token",get)

    def test_dispatcher_pipeline_with_mocked_services(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            src=root/"source"
            src.mkdir()
            output=root/"out"
            output.mkdir()
            target=src/".codex"/"agents"
            target.mkdir(parents=True)
            for role in ROLES:
                mode="workspace-write" if role=="audit" else expected(role)
                (target/(role+".toml")).write_text(f'name = "{role}"\nsandbox_mode = "{mode}"\ndeveloper_instructions = "inspect"\n')
            def git(*args):
                return subprocess.check_output(["git",*args],cwd=src,stderr=subprocess.DEVNULL,text=True).strip()
            git("init")
            git("add",".")
            git("-c","user.name=Test","-c","user.email=test@example.invalid","commit","-m","fixture")
            sha=git("rev-parse","HEAD")
            db=root/"jobs.db"
            enqueue(db,"pilot",1128,sha)
            def agent(role,cwd,sha,**kwargs):
                return {"status":"PASS","role":role,"report":"simulated report","independent_verdict":False}
            with patch("brs_engineering_dispatcher.snapshot",return_value={"status":"PASS"}), patch("brs_engineering_dispatcher.codex_role",side_effect=agent) as mock:
                for _ in range(7):
                    self.assertTrue(tick(db,"pilot",src,output))
                self.assertFalse(tick(db,"pilot",src,output))
                self.assertEqual(mock.call_count,5)
            rows={x["id"]:x for x in status(db,"pilot")}
            self.assertTrue(all(x["state"]=="DONE" for x in rows.values()))
            self.assertFalse(rows["audit"]["result"]["independent_verdict"])
            self.assertEqual(git("status","--porcelain"),"")
            verify_candidate(output/"pilot",rows["candidate"]["result"])
            (output/"pilot"/".codex"/"agents"/"audit.toml").write_text("changed")
            with self.assertRaises(ValueError):
                verify_candidate(output/"pilot",rows["candidate"]["result"])

if __name__=="__main__":
    unittest.main()
