from __future__ import annotations

import copy
import unittest

from scripts.news_editorial_guard import (
    audit_payload, identity, interview_relation, pair_reason,
    preview_frame, repair_sections, suppress_previews,
)


def article(title, summary, link, when, *, source="Polsat News", event=""):
    return {
        "title": title, "summary": summary, "link": link, "source": source,
        "published_at": when, "canonical_event_id": event,
        "image": "https://example.org/photo.jpg",
    }


def motyka():
    preview = article(
        'Miłosz Motyka w programie "Graffiti"',
        "Gościem piątkowego wydania Graffiti będzie minister energii Miłosz Motyka. "
        "Program poprowadzi Marcin Fijołek. Transmisja w Polsacie od 7:40.",
        "https://www.polsatnews.pl/wiadomosc/2026-11-27/milosz-motyka-w-programie-graffiti-ogladaj-od-740-2/",
        "2026-11-27T05:39:00+00:00", event="preview_event",
    )
    report = article(
        '"Szkodzi Polakom". Minister uderzył w prezydenta, w tle głośna ustawa',
        "- Może pan prezydent dołoży się ze swoich? - pytał w piątkowym Graffiti Miłosz Motyka, "
        "mówiąc o kwestii ustawy zmieniającej progi podatkowe oraz zasady opodatkowania CIT. "
        "- Nie zgadzam się na to - mówił Motyka.",
        "https://www.polsatnews.pl/wiadomosc/2026-11-27/szkodzi-polakom-minister-uderzyl-w-prezydenta-w-tle-glosna-ustawa/",
        "2026-11-27T07:15:00+00:00", event="different_report_event",
    )
    return preview, report


class NewsEditorialGuardTests(unittest.TestCase):
    def test_actual_motyka_preview_replaced_by_report_regardless_of_order(self):
        preview, report = motyka()
        self.assertEqual(preview_frame(preview), ("milosz motyka", "graffiti"))
        self.assertEqual(interview_relation(preview, report), "first_preview")
        self.assertEqual(interview_relation(report, preview), "second_preview")
        for rows in ([preview, report], [report, preview]):
            excluded, findings = suppress_previews([rows])
            self.assertEqual(excluded, {identity(preview)})
            self.assertEqual(findings[0]["kept_link"], report["link"])
            cleaned, _ = repair_sections({"polityka": rows})
            self.assertEqual(cleaned["polityka"], [report])

    def test_redundancy_detected_in_reserve_and_across_desks(self):
        preview, report = motyka()
        report_audit = audit_payload({
            "marker": "test",
            "sections": {"polityka": [preview], "ekonomia": [report]},
            "home": [], "home_reserve": [report, preview],
        })
        self.assertEqual(report_audit["status"], "failed")
        self.assertEqual({f["scope"] for f in report_audit["findings"]},
                         {"sections", "homepage_plus_reserve"})
        self.assertTrue(all(f["kept_link"] == report["link"]
                            for f in report_audit["findings"]))

    def test_preview_in_home_reserve_obsolete_even_if_report_only_in_desk(self):
        preview, report = motyka()
        result = audit_payload({"sections": {"polityka": [report]},
                                "home": [], "home_reserve": [preview]})
        self.assertEqual(result["findings"][0]["scope"], "all_public_surfaces")

    def test_intentional_home_and_section_copy_not_duplicate(self):
        _, report = motyka()
        self.assertEqual(audit_payload({"sections": {"polityka": [report]},
                                       "home": [copy.deepcopy(report)],
                                       "home_reserve": []})["status"], "clean")

    def test_same_person_different_program_or_day_does_not_merge(self):
        preview, report = motyka()
        for changes in (
            {"summary": report["summary"].replace("Graffiti", "Gość Wydarzeń")},
            {"published_at": "2026-11-28T07:15:00+00:00"},
            {"published_at": "2026-11-27T04:15:00+00:00"},
            {"summary": report["summary"].replace("Miłosz Motyka", "Marcin Fijołek")},
            {"image": ""},
            {"published_at": ""},
        ):
            different = dict(report, **changes)
            self.assertEqual(interview_relation(preview, different), "")
            self.assertEqual(suppress_previews([[preview, different]])[0], set())

    def test_distinct_substantive_reports_not_removed_just_by_guest_and_show(self):
        _, report = motyka()
        other = article("Minister zapowiedział nową inwestycję w sieci energetyczne",
                        "W programie Graffiti Miłosz Motyka powiedział o budowie linii przesyłowych.",
                        "https://example.org/different", "2026-11-27T07:20:00+00:00",
                        event="another_fact")
        self.assertEqual(interview_relation(report, other), "")
        self.assertEqual(pair_reason(report, other), "")

    def test_english_interview_with_other_guest_and_publisher(self):
        preview = article('Jane Smith on the show "Morning Focus"',
                          "Jane Smith will be a guest. Tune in at 8:00.",
                          "https://example.org/preview", "2026-11-27T06:00:00+00:00",
                          source="Broadcaster A", event="a")
        report = article("Minister sets out the energy plan",
                         "Jane Smith told Morning Focus that the new plan would start in March.",
                         "https://example.net/report", "2026-11-27T08:30:00+00:00",
                         source="Broadcaster B", event="b")
        self.assertEqual(interview_relation(preview, report), "first_preview")

    def test_exact_article_and_canonical_event_not_duplicated(self):
        _, report = motyka()
        self.assertEqual(pair_reason(report, dict(report, link=report["link"]+"?utm_source=x")),
                         "same_source_article")
        self.assertEqual(pair_reason(report, dict(report, link="https://example.org/another")),
                         "same_canonical_event")

    def test_missing_verified_report_keeps_preview(self):
        preview, _ = motyka()
        repaired, findings = repair_sections({"polityka": [preview]})
        self.assertEqual(repaired["polityka"], [preview])
        self.assertFalse(findings)


if __name__ == "__main__":
    unittest.main()
