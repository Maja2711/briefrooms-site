from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from scripts.dedupe_home_brief_stories import same_reported_statement, same_topic
from scripts.news_event_intelligence_v4 import cluster_events


def reports():
    return [
        {
            "source": "TVN24",
            "publisher_source": "TVN24",
            "title": '"Ostatnia rzecz, jakiej Polska potrzebuje". Ambasador USA przestrzega',
            "summary": (
                'Tom Rose, ambasador USA w Polsce, ocenił, że wydarzenia wokół '
                'Trybunału Konstytucyjnego są "bardzo poważne". Napisał '
                'w serwisie X, że obie strony przedstawiają odmienne relacje.'
            ),
            "link": "https://tvn24.pl/polska/ambasador-usa-rose-tk",
            "image": "https://tvn24.pl/images/rose.jpg",
            "published_at": "2026-10-30T22:51:17+00:00",
        },
        {
            "source": "Polsat News",
            "publisher_source": "Polsat News",
            "title": 'Ambasador USA reaguje na wydarzenia przed TK. "Bezprecedensowa konfrontacja"',
            "summary": (
                "Ambasador USA w Polsce Thomas Rose odniósł się do sytuacji "
                "wokół polskiego Trybunału Konstytucyjnego. Zaapelował, "
                "by nie podejmować działań prowadzących do eskalacji."
            ),
            "link": "https://www.polsatnews.pl/wiadomosc/2026-10-31/ambasador-usa-reaguje/",
            "image": "https://polsatnews.pl/images/rose.jpg",
            "published_at": "2026-10-31T00:02:00+00:00",
        },
        {
            "source": "Rzeczpospolita",
            "publisher_source": "Rzeczpospolita",
            "title": "Ambasador USA w Polsce o sporze o TK: Konfrontacja to ostatnie, czego Polska potrzebuje",
            "summary": (
                "Zależy nam na stabilności i demokratycznej przyszłości kraju "
                "– oświadczył ambasador USA w Polsce Thomas Rose, komentując "
                "wydarzenia w Trybunale Konstytucyjnym."
            ),
            "link": "https://www.rp.pl/dyplomacja/rose-trybunal",
            "image": "https://rp.pl/images/rose.jpg",
            "published_at": "2026-10-30T22:58:00+00:00",
        },
    ]


class StatementDedupeRegressionTests(unittest.TestCase):
    def test_three_publishers_same_ambassador_statement_one_topic(self):
        a, b, c = reports()
        self.assertTrue(same_reported_statement(a, b))
        self.assertTrue(same_reported_statement(b, c))
        self.assertTrue(same_topic(a, b))
        self.assertTrue(same_topic(a, c))

    def test_canonical_event_clustering_keeps_one_report(self):
        rows, diagnostics = cluster_events(reports())
        self.assertEqual(len(rows), 1)
        self.assertEqual(diagnostics["event_duplicates_suppressed"], 2)
        self.assertEqual(rows[0]["event_cluster_size"], 3)
        self.assertEqual(
            set(rows[0]["event_publishers"]),
            {"TVN24", "Polsat News", "Rzeczpospolita"},
        )

    def test_different_speakers_and_sub_events_are_not_merged(self):
        ambassador = reports()[0]
        president = deepcopy(ambassador)
        president.update(
            source="Polsat News",
            title="Prezydent Karol Nawrocki spotka się z sędziami Trybunału Konstytucyjnego",
            summary="Prezydent Nawrocki ogłosił spotkanie w budynku TK.",
        )
        self.assertFalse(same_reported_statement(ambassador, president))
        self.assertFalse(same_topic(ambassador, president))
        same_speaker_other_issue = deepcopy(ambassador)
        same_speaker_other_issue.update(
            title="Ambasador USA Tom Rose komentuje decyzję NATO",
            summary="Thomas Rose, ambasador USA, oświadczył, że sojusz NATO musi zwiększyć bezpieczeństwo.",
        )
        self.assertFalse(same_reported_statement(ambassador, same_speaker_other_issue))
        later = deepcopy(ambassador)
        later["published_at"] = (
            datetime.fromisoformat(ambassador["published_at"]) + timedelta(days=2)
        ).isoformat()
        self.assertFalse(same_reported_statement(ambassador, later))

    def test_unverified_times_do_not_cause_a_forced_merge(self):
        a, b, _ = reports()
        a.pop("published_at")
        self.assertFalse(same_reported_statement(a, b))


if __name__ == "__main__":
    unittest.main()
