from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts import publish_live_news as base
from scripts import publish_live_news_filtered as news


def story(title: str, index: int, now: datetime) -> dict:
    return {
        "title": title,
        "link": f"https://example.com/sport-{index}",
        "image": f"https://example.com/sport-{index}.jpg",
        "source": f"Source {index % 4}",
        "summary": title,
        "published_at": (now - timedelta(minutes=index)).isoformat(),
        "published_at_basis": "source",
    }


class PolishSportDiversityTests(unittest.TestCase):
    def test_target_and_watchlist(self) -> None:
        self.assertEqual(base.TARGET, 9)
        names = {item["name"] for item in news.TRACKED_ATHLETES}
        self.assertIn("Hubert Hurkacz", names)
        self.assertIn("Maja Chwalińska", names)

    def test_graduated_priority(self) -> None:
        now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        hurkacz = story("Hubert Hurkacz w turnieju ATP", 1, now)
        chwalinska = story("Maja Chwalińska w turnieju WTA", 1, now)
        self.assertGreater(
            news.sport_hot_score(hurkacz, now),
            news.sport_hot_score(chwalinska, now),
        )

    def test_diverse_nine_with_hard_per_athlete_cap(self) -> None:
        now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        sport = [
            story(f"Iga Świątek wiadomość {i} z US Open", i, now)
            for i in range(6)
        ] + [
            story("Hubert Hurkacz wraca do ATP", 20, now),
            story("Maja Chwalińska awansuje w WTA", 21, now),
            story("Robert Lewandowski zdobywa bramkę dla Barcelony", 22, now),
            story("Tomasz Fornal prowadzi Polskę w siatkówce", 23, now),
            story("Bartosz Zmarzlik wygrywa Grand Prix na żużlu", 24, now),
            story("Natalia Bukowiecka w finale 400 m", 25, now),
            story("Katarzyna Niewiadoma na trasie wyścigu kolarskiego", 26, now),
            story("Polski zespół z ważnym zwycięstwem", 27, now),
        ]
        politics = [story(f"Polityka {i}", 100 + i, now) for i in range(9)]
        selected, health = news.select_sections(
            [("polityka", "Polityka", []), ("sport", "Sport", [])],
            {"polityka": politics, "sport": sport},
            {"sections": {}},
            now,
        )
        rows = selected["sport"]
        self.assertEqual(len(rows), 9)
        self.assertLessEqual(sum("Świątek" in item["title"] for item in rows), 2)
        titles = " ".join(item["title"] for item in rows)
        self.assertIn("Hubert Hurkacz", titles)
        self.assertIn("Maja Chwalińska", titles)
        tracked = {
            athlete["name"]
            for item in rows
            for athlete in news._matched_tracked_athletes(item)
        }
        self.assertGreaterEqual(len(tracked), 5)
        self.assertEqual(health["sport"]["diversity_policy"], "pl-sport-diversity-v1")

    def test_carried_stories_cannot_bypass_athlete_cap(self) -> None:
        now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        fresh = [story(f"Iga Świątek news {i}", i, now) for i in range(2)]
        fresh += [
            story(title, 20 + i, now)
            for i, title in enumerate((
                "Adam Nowak wygrał zawody",
                "Bartosz Kowal zdobył medal",
                "Celina Lis awansowała do finału",
                "Dawid Król ustanowił rekord",
                "Ewa Mazur prowadzi w klasyfikacji",
                "Filip Wilk wraca do reprezentacji",
            ))
        ]
        previous = {
            "sections": {
                "sport": [
                    story(f"Iga Świątek starsza wiadomość {i}", 100 + i, now)
                    for i in range(4)
                ] + [story("Hubert Hurkacz wraca do gry", 110, now)]
            }
        }
        politics = [story(f"Polityka {i}", 200 + i, now) for i in range(9)]
        selected, _ = news.select_sections(
            [("polityka", "Polityka", []), ("sport", "Sport", [])],
            {"polityka": politics, "sport": fresh},
            previous,
            now,
        )
        self.assertEqual(len(selected["sport"]), 9)
        self.assertEqual(sum("Świątek" in item["title"] for item in selected["sport"]), 2)
        self.assertTrue(any("Hurkacz" in item["title"] for item in selected["sport"]))

    def test_cross_source_entity_cannot_fill_three_cards(self) -> None:
        now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        sport = [
            story("Raków Częstochowa ogłosił nowego trenera", 1, now),
            story("Raków podjął decyzję w sprawie trenera", 2, now),
            story("Raków Częstochowa ma następcę trenera", 3, now),
        ] + [
            story(title, 20 + i, now)
            for i, title in enumerate((
                "Adam Nowak wygrał zawody",
                "Bartosz Kowal zdobył medal",
                "Celina Lis awansowała do finału",
                "Dawid Król ustanowił rekord",
                "Ewa Mazur prowadzi w klasyfikacji",
                "Filip Wilk wraca do reprezentacji",
                "Grzegorz Dudek podpisał kontrakt",
                "Hanna Wójcik poprawiła rekord",
            ))
        ]
        politics = [story(f"Polityka {i}", 100 + i, now) for i in range(9)]
        selected, _ = news.select_sections(
            [("polityka", "Polityka", []), ("sport", "Sport", [])],
            {"polityka": politics, "sport": sport},
            {"sections": {}},
            now,
        )
        self.assertEqual(len(selected["sport"]), 9)
        self.assertLessEqual(sum("Raków" in item["title"] for item in selected["sport"]), 2)


if __name__ == "__main__":
    unittest.main()
