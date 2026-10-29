"""Mocked service transport only; no endpoint is deployed by these tests."""
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eurusd_cb_poll_service as service
import eurusd_central_bank_wire as wire
from tests.test_eurusd_central_bank_wire import Client, NOW, collected


class PollServiceTests(unittest.TestCase):
    def test_single_poll_uses_shared_collector_and_durable_original_journal(self):
        original = collected(Client(), calendar=[])
        clock = Mock(return_value=NOW)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wire.json'
            with patch.object(wire, 'collect_calendars', return_value=([], original['calendar_coverage'])) as calendar, \
                 patch.object(wire, 'collect', wraps=wire.collect) as collect:
                result = service.run_once(path, client=Client(), clock=clock)
            self.assertTrue(wire.validate_snapshot(json.loads(path.read_text()), NOW))
            self.assertEqual(collect.call_count, 1)
            self.assertEqual(calendar.call_count, 1)
            self.assertEqual(len(list((path.parent / 'eurusd_cb_wire_journal').glob('*.json'))), 1)
            self.assertFalse(result['published'])
            self.assertEqual(result['target_poll_seconds'], 60)
            self.assertIn('NOT_LIVE_SPEECH', result['latency_claim'])
            before = path.read_bytes()
            with patch.object(wire, 'collect_calendars', return_value=([], original['calendar_coverage'])):
                repeat = service.run_once(path, client=Client(), clock=clock)
            self.assertFalse(repeat['saved'])
            self.assertEqual(path.read_bytes(), before)

    def test_optional_publisher_receives_exact_canonical_snapshot_after_save(self):
        original = collected(Client(), calendar=[])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wire.json'
            def publish(snapshot, url, token):
                self.assertTrue(path.exists())
                self.assertEqual(wire.load_wire(path, NOW), snapshot)
                self.assertEqual(token, 'synthetic-token-never-real')
                return {'published': True}
            with patch.object(wire, 'collect_calendars', return_value=([], original['calendar_coverage'])):
                result = service.run_once(path, client=Client(), clock=lambda: NOW,
                    publish_url='synthetic-mocked-endpoint', token='synthetic-token-never-real', publisher=publish)
            self.assertTrue(result['published'])

    def test_authenticated_publish_accepts_only_dedicated_https_endpoint_no_redirect(self):
        valid = 'https://eurusd-cb-wire.synthetic.workers.dev/v1/wire'
        self.assertEqual(service.validate_publish_url(valid), valid)
        for url in ('http://eurusd-cb-wire.synthetic.workers.dev/v1/wire',
                    'https://shared-trading.synthetic.workers.dev/v1/wire',
                    'https://127.0.0.1/v1/wire', 'https://eurusd-cb-wire.synthetic.workers.dev/v1/wire?token=bad',
                    'https://secret@eurusd-cb-wire.synthetic.workers.dev/v1/wire',
                    'https://eurusd-cb-wire.synthetic.workers.dev/sync-trading'):
            with self.assertRaises(ValueError): service.validate_publish_url(url)
        with self.assertRaises(ValueError):
            service._NoRedirect().redirect_request(None, None, 302, '', {}, valid)

    def test_publisher_validates_raw_receipt_and_never_reinterprets_python_hash(self):
        snapshot = collected(Client(), calendar=[])
        transport = Mock()
        def open_request(request, timeout):
            self.assertEqual(timeout, 10)
            self.assertEqual(request.method, 'POST')
            expected = hashlib.sha256(request.data).hexdigest()
            self.assertEqual(request.headers['X-body-sha256'], expected)
            response = Mock(status=201)
            response.read.return_value = json.dumps({'body_sha256': expected, 'received_at': wire.iso(NOW)}).encode()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            return response
        transport.open.side_effect = open_request
        result = service.publish_snapshot(snapshot, 'https://eurusd-cb-wire.synthetic.workers.dev/v1/wire',
            'synthetic-test-token-32-characters-minimum', opener=transport)
        self.assertTrue(result['published'])

    def test_loop_backoff_logs_type_only_and_stops_gracefully(self):
        stop = Mock(); stop.is_set.return_value = False; stop.wait.return_value = True
        once = Mock(side_effect=RuntimeError('DO_NOT_LOG_SECRET'))
        emitted = []
        service.run_service('synthetic-output', stop=stop, once=once, emit=emitted.append,
            monotonic=Mock(side_effect=[0.0, 1.0]))
        self.assertEqual(stop.wait.call_args.args, (119.0,))
        self.assertNotIn('DO_NOT_LOG_SECRET', ''.join(emitted))
        self.assertIn('RuntimeError', emitted[0])
        with self.assertRaises(ValueError):
            service.run_service('synthetic', stop=stop, interval_seconds=5)

    def test_expired_cycle_budget_stops_http_work(self):
        client = service.DeadlineClient(budget_seconds=2, monotonic=Mock(side_effect=[0.0, 3.0]))
        with self.assertRaises(TimeoutError):
            client.get(wire.OFFICIAL_RSS['fed_monetary'])


if __name__ == '__main__': unittest.main()
