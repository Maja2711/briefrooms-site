from __future__ import annotations

import copy
import unittest

from scripts.axiom_thought_guard import similarity, validate
from scripts.publish_axiom_thought import candidate_validation_stamp


CURRENT = {
    "date": "2026-09-16",
    "author": "AXIOM",
    "brand": "BriefRooms",
    "pl": "„Najtrudniej zauważyć granicę własnego myślenia, bo wszystko, co znamy, znajduje się po jej wewnętrznej stronie.”",
    "en": "“The hardest boundary to notice is the boundary of our own thinking, because everything we know lies on its inner side.”",
}

SEED = {
    "date": "2026-09-15",
    "theme": "future-and-decision",
    "pl": "„Przyszłość rzadko zaczyna się od wielkiego przełomu — częściej od jednej decyzji, której nikt poza tobą jeszcze nie rozumie.”",
    "en": "“The future rarely begins with a great breakthrough — more often, it begins with one decision that nobody but you understands yet.”",
    "seed": True,
    "candidates_considered": 0,
    "scores": {"depth": 9, "novelty": 10, "banality_risk": 1},
    "silence_test": True,
    "editor_note": "Seed series entry used to bootstrap long-term memory and thematic comparison for future Morning AXIOM publications.",
}

LATEST = {
    "date": "2026-09-16",
    "theme": "knowledge-and-limits",
    "pl": CURRENT["pl"],
    "en": CURRENT["en"],
    "candidates_considered": 5,
    "scores": {"depth": 9, "novelty": 9, "banality_risk": 1},
    "silence_test": True,
    "editor_note": "The thought turns the usual idea of an external boundary inward and makes the reader question the invisible frame of what can be known.",
}


class AxiomThoughtGuardTests(unittest.TestCase):
    def test_valid_publication_passes(self) -> None:
        self.assertEqual(validate(CURRENT, [SEED, LATEST]), [])

    def test_current_and_history_must_match(self) -> None:
        history = [SEED, copy.deepcopy(LATEST)]
        history[-1]["pl"] = "„Inna myśl, która celowo nie odpowiada plikowi bieżącemu i dlatego powinna zostać odrzucona przez bramkę.”"
        errors = validate(CURRENT, history)
        self.assertTrue(any("must match current thought" in e for e in errors))

    def test_banal_motivational_phrase_is_rejected(self) -> None:
        current = copy.deepcopy(CURRENT)
        current["pl"] = "„Uwierz w siebie, bo każdy dzień jest nową szansą, aby zmienić swoje życie na lepsze i osiągnąć sukces.”"
        history = [SEED, copy.deepcopy(LATEST)]
        history[-1]["pl"] = current["pl"]
        errors = validate(current, history)
        self.assertTrue(any("banality phrase" in e for e in errors))

    def test_low_editorial_scores_are_rejected(self) -> None:
        history = [SEED, copy.deepcopy(LATEST)]
        history[-1]["scores"] = {"depth": 7, "novelty": 6, "banality_risk": 4}
        errors = validate(CURRENT, history)
        self.assertTrue(any("depth must be >= 8" in e for e in errors))
        self.assertTrue(any("novelty must be >= 8" in e for e in errors))
        self.assertTrue(any("banality_risk must be <= 2" in e for e in errors))

    def test_fewer_than_five_candidates_is_rejected(self) -> None:
        history = [SEED, copy.deepcopy(LATEST)]
        history[-1]["candidates_considered"] = 3
        errors = validate(CURRENT, history)
        self.assertTrue(any("at least 5 candidates" in e for e in errors))

    def test_recent_theme_repetition_is_rejected(self) -> None:
        repeated = copy.deepcopy(LATEST)
        repeated["theme"] = SEED["theme"]
        errors = validate(CURRENT, [SEED, repeated])
        self.assertTrue(any("repeats within" in e for e in errors))

    def test_same_theme_is_rejected_for_full_365_day_window(self) -> None:
        current = copy.deepcopy(CURRENT)
        current["date"] = "2027-09-15"
        latest = copy.deepcopy(LATEST)
        latest.update({"date": current["date"], "theme": SEED["theme"], "pl": current["pl"], "en": current["en"]})
        seed = copy.deepcopy(SEED)
        seed["date"] = "2026-09-15"
        errors = validate(current, [seed, latest])
        self.assertTrue(any("protected 365-day history" in e for e in errors))

    def test_theme_may_expire_only_after_365_days(self) -> None:
        current = copy.deepcopy(CURRENT)
        current["date"] = "2027-09-16"
        latest = copy.deepcopy(LATEST)
        latest.update({"date": current["date"], "theme": SEED["theme"], "pl": current["pl"], "en": current["en"]})
        seed = copy.deepcopy(SEED)
        seed["date"] = "2026-09-15"
        errors = validate(current, [seed, latest])
        self.assertFalse(any("protected 365-day history" in e for e in errors))

    def test_near_duplicate_is_rejected(self) -> None:
        current = copy.deepcopy(CURRENT)
        current["date"] = "2026-09-17"
        current["pl"] = "„Przyszłość często zaczyna się nie od przełomu, lecz od jednej decyzji, której inni jeszcze nie potrafią zrozumieć.”"
        current["en"] = "“The future often begins not with a breakthrough, but with one decision that others cannot yet understand.”"
        latest = copy.deepcopy(LATEST)
        latest.update({
            "date": current["date"],
            "theme": "risk-and-choice",
            "pl": current["pl"],
            "en": current["en"],
        })
        errors = validate(current, [SEED, latest])
        self.assertTrue(any("too similar to history" in e for e in errors))

    def test_reserve_validation_moves_past_already_published_date(self) -> None:
        rows = [
            {"date": "2026-09-25"},
            {"date": "2026-09-26"},
        ]
        self.assertEqual(candidate_validation_stamp(rows, "2026-09-26"), "2026-09-27")

    def test_reserve_validation_keeps_unused_publication_date(self) -> None:
        rows = [{"date": "2026-09-25"}]
        self.assertEqual(candidate_validation_stamp(rows, "2026-09-26"), "2026-09-26")

    def test_similarity_ranks_paraphrase_above_new_idea(self) -> None:
        paraphrase = "Przyszłość często zaczyna się od jednej niezrozumianej decyzji, a nie od wielkiego przełomu."
        novel = "Najtrudniej zauważyć granicę własnego myślenia, bo cały znany świat oglądamy z jej wnętrza."
        paraphrase_similarity = similarity(SEED["pl"], paraphrase)
        novel_similarity = similarity(SEED["pl"], novel)
        self.assertGreater(paraphrase_similarity.sequence, novel_similarity.sequence)
        self.assertGreater(paraphrase_similarity.jaccard, novel_similarity.jaccard)
        self.assertLess(novel_similarity.jaccard, 0.45)


if __name__ == "__main__":
    unittest.main()
