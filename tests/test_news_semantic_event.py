from __future__ import annotations

import unittest
from scripts.news_semantic_event import VERSION, event_signature, same_specific_event, duplicate_pairs
from scripts.dedupe_home_brief_stories import same_topic
from scripts.news_event_intelligence_v4 import cluster_events


def article(title, summary, source, time="2026-10-30T20:00:00+00:00"):
    return {
        "title": title, "summary": summary,
        "source": source, "publisher_source": source,
        "published_at": time,
        "link": f"https://example.com/{source.replace(' ', '-')}/{abs(hash(title))}",
        "image": "https://example.com/image.jpg",
    }


class SharedSemanticEventTests(unittest.TestCase):
    def test_regression_pl_pajor_award_votes_different_publishers(self):
        first = article(
            "Wszystko jasne. Wiadomo, ilu głosów zabrakło Pajor do Złotej Piłki",
            "Alexia Putellas zdobyła Złotą Piłkę, wyprzedzając Ewę Pajor o 86 punktów. Poznaliśmy wyniki głosowania.",
            "SportoweFakty WP",
        )
        second = article(
            "O tyle punktów Ewa Pajor przegrała Złotą Piłkę. Ujawnili wyniki",
            "Ewa Pajor przegrała walkę o tytuł najlepszej piłkarki o 86 pkt. Szczegółowe wyniki głosowania.",
            "Przegląd Sportowy / Onet Sport",
        )
        self.assertTrue(same_specific_event(first, second))
        self.assertTrue(same_topic(first, second))
        score_omitted = article(
            "Znamy dokładne wyniki Złotej Piłki. Niewielka różnica między Ewą Pajor a Alexią Putellas",
            "Znamy dokładne wyniki Złotej Piłki. Niewielka różnica między Ewą Pajor a Alexią Putellas",
            "TVP Sport", "2026-10-31T06:14:00+00:00",
        )
        self.assertTrue(same_specific_event(first, score_omitted))
        another_publisher = article(
            "Pajor była tak blisko Złotej Piłki. Ujawnili punktację, już wszystko jasne",
            "Gala Złotej Piłki przyniosła wielkie emocje. "
            "Ewa Pajor walczyła o nagrodę z Alexią Putellas, a France Football "
            "ujawniło, ile punktów zabrakło reprezentantce.",
            "Interia Sport", "2026-10-30T21:43:47+00:00",
        )
        self.assertTrue(same_specific_event(first, another_publisher))
        self.assertEqual(len(cluster_events([first, second, score_omitted, another_publisher])[0]), 1)
        rows, diag = cluster_events([first, second])
        self.assertEqual(len(rows), 1)
        self.assertEqual(diag["event_duplicates_suppressed"], 1)

        other = article(
            "Mbappe był o włos od Złotej Piłki. Oto szczegóły głosowania",
            "Kylian Mbappe zajął drugie miejsce w męskim plebiscycie.",
            "RMF24 Sport",
        )
        self.assertFalse(same_specific_event(first, other))

    def test_regression_pl_same_inflation_release(self):
        first = article("Inflacja w październiku spadła. Minister wskazał przyczynę",
                        "Inflacja w październiku spadła. Minister wskazał przyczynę", "RMF24")
        second = article("Inflacja wyhamowała mocniej od prognoz. GUS podał dane za październik",
                         "Inflacja wyhamowała mocniej od prognoz. GUS podał dane za październik", "RMF24")
        self.assertTrue(same_specific_event(first, second))
        different_month = article("Inflacja w listopadzie spadła", "Nowe dane za listopad", "RMF24")
        self.assertFalse(same_specific_event(first, different_month))

    def test_regression_en_g20_photo(self):
        a = article("G20 'family photo' cancelled after protests over presence of Russian minister",
                    "UK foreign minister criticised Russia", "BBC News")
        b = article("US cancels G20 ‘family photo’ after Australia, UK and others refuse to appear with Russia",
                    "Foreign ministers threatened a boycott and family photo was cancelled", "The Guardian")
        self.assertTrue(same_specific_event(a, b))
        briefing = article("Ukraine war briefing: Moscow’s Lavrov snubbed at G20 in Atlanta",
                           "Plans for a traditional family photo abandoned", "The Guardian")
        self.assertFalse(same_specific_event(a, briefing))

    def test_regression_en_newspaper_closure_across_desks(self):
        a = article("They strangled us, says founder of Russian newspaper shut under pressure from authorities",
                    "Novaya Gazeta has been one of the most high-profile independent news outlets in Russia",
                    "BBC News")
        b = article("Russian society being systematically crushed says Novaya Gazeta as newspaper announces closure",
                    "Novaya Gazeta permanently closing next month", "The Guardian")
        c = article("Russia’s Novaya Gazeta ‘killed off’ after murders, arrests and pressure",
                    "Investigative newspaper Novaya Gazeta is closing", "Al Jazeera English")
        self.assertTrue(same_specific_event(a, b))
        self.assertTrue(same_specific_event(a, c))
        self.assertEqual(len(cluster_events([a, b, c])[0]), 1)
        self.assertEqual(len(duplicate_pairs([a, b, c])), 2)
        unrelated = article("Newspaper editor arrested in Russia",
                            "Another publication faces a legal challenge", "BBC News")
        self.assertFalse(same_specific_event(a, unrelated))

    def test_regression_en_patriot_sale_cross_section(self):
        a = article("US approves sale of 500 Patriot missiles to Ukraine",
                    "The interceptors down Russian ballistic missiles", "BBC News")
        b = article("Ukraine’s Zelenskyy praises US over $2.1bn Patriot missile deal but warns it could take years",
                    "The US has approved the sale of 500 Patriot PAC-3 missiles to Ukraine for $2.1bn", "The Guardian")
        self.assertTrue(same_specific_event(a, b))
        different = article("US approves 700 Patriot missiles to Ukraine",
                            "A new sale of 700 missiles is approved", "BBC News")
        self.assertFalse(same_specific_event(a, different))

    def test_regression_pl_court_entry_but_not_statements_or_liveblog(self):
        a = article("Prezydent wszedł do siedziby TK. Doszło do szarpaniny",
                    "Prezydent Karol Nawrocki przybył do Trybunału Konstytucyjnego", "Polsat News")
        b = article("Karol Nawrocki siłą wdarł się w nocy do Trybunału Konstytucyjnego",
                    "Prezydent pojawił się w budynku Trybunału Konstytucyjnego",
                    "Business Insider Polska")
        self.assertTrue(same_specific_event(a, b))
        opinion = article("Minister sprawiedliwości o prezydencie w trybunale",
                          "Minister skrytykował Karola Nawrockiego po wydarzeniach w Trybunale Konstytucyjnym",
                          "TVN24")
        self.assertFalse(same_specific_event(a, opinion))
        liveblog = article("Chaos wokół Trybunału Konstytucyjnego, prezydent zwołuje konsultacje. Relacja na żywo",
                           "Karol Nawrocki wszedł do TK, sędzia ogłosiła zawieszenie prezesa", "Rzeczpospolita")
        self.assertFalse(same_specific_event(a, liveblog))

    def test_different_dates_or_missing_provenance_do_not_merge(self):
        a = article("US approves sale of 500 Patriot missiles to Ukraine",
                    "Sale has been approved", "BBC News")
        b = article("US approves sale of 500 Patriot missiles to Ukraine",
                    "Sale has been approved", "The Guardian", "2026-11-04T20:00:00+00:00")
        self.assertFalse(same_specific_event(a, b))
        b.pop("published_at")
        self.assertFalse(same_specific_event(a, b))
        self.assertEqual(VERSION, "semantic-event-frames-v1")


if __name__ == "__main__":
    unittest.main()
