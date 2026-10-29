"""Synthetic restart/conference fixtures. No market, trade or history writes."""
import copy
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import eurusd_central_bank_wire as wire
import eurusd_central_bank_event_risk as risk
from tests.test_eurusd_central_bank_wire import (
    Client, collected, feed, NOW, CALENDAR, URL, DOC, ECB_URL,
)


def calendar(now=NOW):
    return [{**CALENDAR[0], 'event_at': wire.iso(NOW-timedelta(hours=2)),
        'time_precision': 'exact', 'first_seen_at': wire.iso(NOW-timedelta(hours=3)),
        'calendar_checked_at': wire.iso(now)}]


def assess(snapshot, now):
    return risk.assess_pre_event(None, None, snapshot, now=now,
        policy=copy.deepcopy(risk.DEFAULT_POLICY), scope='EURUSD_WEEKLY')


class UnresolvedWindowTests(unittest.TestCase):
    def test_unknown_conference_survives_next_day_and_real_snapshot_restart(self):
        first = collected(Client(), now=NOW, calendar=calendar())
        self.assertTrue(first['unresolved_event_windows'])
        original = copy.deepcopy(first)
        later = NOW+timedelta(days=2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wire.json'
            wire.write_snapshot(path, first)
            restored = wire.load_wire(path, now=later)
            after = collected(Client(), previous=restored, now=later, calendar=[])
            wire.write_snapshot(path, after)
            self.assertTrue(wire.validate_snapshot(wire.load_wire(path, now=later), later))
            journal = [p.read_bytes() for p in (path.parent/'eurusd_cb_wire_journal').glob('*.json')]
            self.assertEqual(len(journal), 2)
        self.assertEqual(first, original)
        self.assertEqual(after['unresolved_event_windows'][0]['event_id'], 'fixture:fomc')
        result = assess(after, later)
        self.assertTrue(result.block_new_entry)
        self.assertIn('RECOVERY_REQUIRED_CONFERENCE_UNCONFIRMED', result.reasons)

    def test_proven_end_after_cooldown_does_not_relatch_on_fresh_unknown_poll(self):
        rows = calendar()
        rows[0].update(conference_status='ENDED', conference_ended_at=wire.iso(NOW-timedelta(minutes=40)),
            conference_status_verified_at=wire.iso(NOW-timedelta(minutes=37)),
            conference_status_source_url=URL,
            conference_end_evidence={'statement_verified': True, 'event_id': 'fixture:fomc',
                'source_url': URL, 'published_at': wire.iso(NOW-timedelta(minutes=39)),
                'first_seen_at': wire.iso(NOW-timedelta(minutes=38)),
                'ingested_at': wire.iso(NOW-timedelta(minutes=37))})
        ended = collected(Client(), now=NOW, calendar=rows)
        self.assertFalse(assess(ended, NOW).block_new_entry)
        later = NOW+timedelta(minutes=1)
        # This is how a routine source-calendar refresh represents the meeting:
        # it has dates, not a transcript/status service.
        after = collected(Client(), previous=ended, now=later, calendar=calendar(later))
        self.assertEqual(after['event_window']['conference_status'], 'UNKNOWN')
        self.assertEqual(after['unresolved_event_windows'][0]['conference_status'], 'ENDED')
        self.assertFalse(assess(after, later).block_new_entry)

    def test_end_without_verifiable_evidence_never_clears_after_48_hours(self):
        rows = calendar()
        rows[0].update(conference_status='ENDED', conference_ended_at=wire.iso(NOW-timedelta(minutes=40)),
            conference_status_verified_at=wire.iso(NOW-timedelta(minutes=39)),
            conference_status_source_url=URL)
        prior = collected(Client(), now=NOW, calendar=rows)
        later = NOW+timedelta(days=2)
        after = collected(Client(), previous=prior, now=later, calendar=[])
        self.assertTrue(assess(after, later).block_new_entry)

    def test_overflow_is_sticky_partial_and_blocks_even_after_fresh_source_checks(self):
        previous = collected(Client(), now=NOW, calendar=[])
        previous['unresolved_event_windows'] = [
            {'event_id': f'synthetic-open-{i}', 'bank': 'FED', 'event_type': 'FOMC_DECISION',
             'scheduled_at': wire.iso(NOW-timedelta(hours=2)), 'source_url': URL,
             'first_seen_at': wire.iso(NOW-timedelta(hours=3)), 'calendar_checked_at': wire.iso(NOW),
             'conference_status': 'UNKNOWN', 'time_precision': 'exact'}
            for i in range(wire.MAX_UNRESOLVED_WINDOWS)]
        previous['integrity']['snapshot_sha256'] = wire.snapshot_hash(previous)
        self.assertTrue(wire.validate_snapshot(previous, NOW))
        later = NOW+timedelta(minutes=1)
        snapshot = collected(Client(), previous=previous, now=later, calendar=calendar(later))
        self.assertTrue(snapshot['unresolved_event_windows_overflow'])
        self.assertEqual(snapshot['overall_status'], 'PARTIAL')
        self.assertTrue(assess(snapshot, later).block_new_entry)
        repeat = collected(Client(), previous=snapshot, now=later+timedelta(minutes=1), calendar=[])
        self.assertTrue(repeat['unresolved_event_windows_overflow'])
        self.assertEqual(repeat['overall_status'], 'PARTIAL')

    def test_resigned_malformed_or_future_windows_are_rejected(self):
        valid = collected(Client(), now=NOW, calendar=calendar())
        mutations = [
            lambda s: s.update(unresolved_event_windows={}),
            lambda s: s.update(unresolved_event_windows_overflow='false'),
            lambda s: s['unresolved_event_windows'].append(copy.deepcopy(s['unresolved_event_windows'][0])),
            lambda s: s['unresolved_event_windows'][0].update(source_url='https://127.0.0.1/private'),
            lambda s: s['unresolved_event_windows'][0].update(first_seen_at=wire.iso(NOW+timedelta(seconds=1))),
            lambda s: s['unresolved_event_windows'][0].update(bank='BOJ'),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                snapshot = copy.deepcopy(valid); mutation(snapshot)
                snapshot['integrity']['snapshot_sha256'] = wire.snapshot_hash(snapshot)
                self.assertFalse(wire.validate_snapshot(snapshot, NOW))

    def test_unscheduled_official_fed_release_creates_its_own_guard(self):
        snapshot = collected(calendar=[])
        window = snapshot['event_window']
        self.assertEqual(window['schedule_kind'], 'UNSCHEDULED_RELEASE')
        self.assertEqual(window['bank'], 'FED')
        self.assertEqual(window['scheduled_at'], snapshot['events'][0]['published_at'])
        self.assertEqual(window['release_proof']['source_url'], URL)
        self.assertTrue(window['release_proof']['statement_verified'])
        self.assertTrue(assess(snapshot, NOW).block_new_entry)

    def test_unscheduled_official_ecb_release_keeps_its_own_source(self):
        document = b'The Governing Council decided to lower rates. The interest rates on the deposit facility, the main refinancing operations and the marginal lending facility will be decreased to 2.00%, 2.15% and 2.40% respectively.'
        snapshot = collected(Client({wire.OFFICIAL_RSS['ecb_press_speeches']:
            feed(title='Monetary policy decisions', url=ECB_URL), ECB_URL: document}), calendar=[])
        window = snapshot['event_window']
        self.assertEqual(window['bank'], 'ECB')
        self.assertEqual(window['event_type'], 'ECB_DECISION')
        self.assertEqual(window['source_url'], ECB_URL)
        self.assertTrue(assess(snapshot, NOW).block_new_entry)

    def test_future_publication_cannot_seed_an_unscheduled_guard_or_proof(self):
        snapshot = collected(Client({wire.OFFICIAL_RSS['fed_monetary']:
            feed(published='Wed, 28 Oct 2026 19:00:00 GMT'), URL: DOC}), calendar=[])
        self.assertEqual(snapshot['events'], [])
        self.assertEqual(snapshot['unresolved_event_windows'], [])
        self.assertIsNone(snapshot['event_window']['event_id'])
        self.assertFalse(assess(snapshot, NOW).block_new_entry)


if __name__ == '__main__': unittest.main()
