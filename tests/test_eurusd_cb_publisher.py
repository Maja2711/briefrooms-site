import base64
import copy
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tests.test_eurusd_central_bank_wire import collected, Client, NOW
import publish_eurusd_cb_wire as publisher


class PublisherTests(unittest.TestCase):
    def request(self, repo,token,method,path,body=None):
        self.calls.append((method,path,body))
        if path=='git/ref/heads/main':return {'object':{'sha':'head'}}
        if path=='git/commits/head':return {'tree':{'sha':'base'}}
        if path.startswith('contents/'):
            if self.remote and 'eurusd_central_bank_wire.json?' in path:
                return {'content':base64.b64encode(json.dumps(self.remote).encode()).decode()}
            raise HTTPError('https://test.invalid',404,'not found',{},None)
        if path=='git/trees':return {'sha':'tree'}
        if path=='git/blobs':return {'sha':'blob'}
        if path=='git/commits':return {'sha':'next'}
        if path=='git/refs/heads/main':
            if self.race:
                self.race=False
                raise HTTPError('https://test.invalid',422,'race',{},None)
            self.assertFalse(body['force']);return {}
        self.fail(path)

    def setUp(self):self.calls=[];self.remote=None;self.race=False

    def test_only_wire_and_immutable_receipt_single_atomic_commit(self):
        snapshot=collected(Client())
        self.assertEqual(publisher.publish(snapshot,'repo','token',self.request),'next')
        tree=next(row[2] for row in self.calls if row[1]=='git/trees')
        self.assertEqual(tree['base_tree'],'base')
        self.assertEqual({e['path'] for e in tree['tree']},{publisher.SNAPSHOT,publisher.JOURNAL+snapshot['integrity']['snapshot_sha256']+'.json'})
        self.assertTrue(all('/actions/' not in row[1] for row in self.calls))

    def test_newer_remote_is_not_replaced(self):
        self.remote=collected(Client())
        self.assertEqual(publisher.publish(self.remote,'repo','token',self.request),'skipped_newer_or_equal')
        self.assertFalse(any(row[0]=='POST' for row in self.calls))

    def test_race_reloads_head_and_never_forces(self):
        self.race=True
        publisher.publish(collected(Client()),'repo','token',self.request)
        self.assertEqual(sum(row[1]=='git/ref/heads/main' for row in self.calls),2)

    def test_corrupt_hash_never_writes(self):
        snapshot=collected(Client());snapshot['overall_status']='UNAVAILABLE'
        with self.assertRaises(ValueError):publisher.publish(snapshot,'repo','token',self.request)
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
