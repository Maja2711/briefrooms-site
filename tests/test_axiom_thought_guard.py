from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.axiom_thought_guard import similarity, validate, review_digest, review_evidence_errors, rhetorical_templates, rhetorical_style_issues, rhetorical_diversity_score
from scripts.publish_axiom_thought import candidate_validation_stamp, semantic_review, semantic_smoke, validate_candidate, publish, main, generate_ten, refill, evidence_valid, attach_review, core_candidate, eligible_reserve, reserve_status, prompt


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
    def test_philosophical_inspiration_reaches_both_creation_modes(self) -> None:
        for mode in ("live", "reserve"):
            with self.subTest(mode=mode):
                instructions = prompt([SEED], [], mode)
                self.assertIn("PHILOSOPHICAL INSPIRATION", instructions)
                self.assertIn("history of philosophy", instructions)
                self.assertIn("DEVELOP YOUR OWN INDEPENDENT REFLECTION", instructions)
                self.assertIn("Do not copy or closely paraphrase famous aphorisms", instructions)
                self.assertIn("names of philosophers", instructions)
                self.assertIn("COMPLETE PUBLISHED HISTORY", instructions)
                self.assertIn(SEED["pl"], instructions)
                self.assertIn("semantic repeat", instructions)

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
        self.assertTrue(any("too similar to full history" in e for e in errors))

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


    def test_identical_text_is_blocked_even_after_retention_window(self) -> None:
        current = copy.deepcopy(CURRENT)
        current["date"] = "2027-09-17"
        latest = copy.deepcopy(LATEST)
        latest.update({"date": current["date"], "theme": "another-theme",
                       "pl": SEED["pl"], "en": SEED["en"]})
        current["pl"], current["en"] = latest["pl"], latest["en"]
        errors = validate(current, [SEED, latest])
        self.assertTrue(any("exact thought duplicate in full history" in e for e in errors), errors)

    def test_semantic_veto_blocks_new_wording_of_old_thesis(self) -> None:
        candidate = {"theme": "public-debate", "pl": "„Zgoda społeczna potrafi wyciszyć niewygodne pytania, nie nakazując nikomu milczenia.”",
                     "en": "“Collective agreement can silence uncomfortable questions without ever ordering anyone to keep quiet.”"}
        verdict = {"verdict": "DUPLICATE", "closest_ref": "2026-09-15",
                   "shared_claim": "Both expressions describe the same social mechanism of suppression.",
                   "material_difference": "No new mechanism is provided; only the imagery and vocabulary have changed."}
        with patch("scripts.publish_axiom_thought.call", return_value=verdict) as mock_call:
            with self.assertRaisesRegex(ValueError, "AXIOM_SEMANTIC_REVIEW_BLOCKED: DUPLICATE"):
                semantic_review(candidate, [SEED])
        mock_call.assert_called_once()

    def test_semantic_unique_requires_a_real_archive_reference(self) -> None:
        candidate = {"theme": "new-topic", "pl": CURRENT["pl"], "en": CURRENT["en"]}
        verdict = {"verdict": "UNIQUE", "closest_ref": "2026-09-15",
                   "shared_claim": "Both statements concern personal decisions and constraints in human knowledge.",
                   "material_difference": "This candidate concerns cognitive blind spots rather than future-forming decisions."}
        with patch("scripts.publish_axiom_thought.call", return_value=verdict) as mock_call:
            receipts = semantic_review(candidate, [SEED])
            self.assertEqual(len(receipts), 2)
            self.assertEqual([r["model"] for r in receipts], ["gemini-3.6-flash", "gemini-3.5-flash-lite"])
            self.assertTrue(all(r["closest_ref"] == "2026-09-15" for r in receipts))
        self.assertEqual(mock_call.call_count, 2)

    def test_uncertain_semantic_verdict_fails_closed(self) -> None:
        candidate = {"theme": "new-topic", "pl": CURRENT["pl"], "en": CURRENT["en"]}
        verdict = {"verdict": "UNCERTAIN", "closest_ref": "2026-09-15",
                   "shared_claim": "There appears to be overlap involving long-term decision consequences.",
                   "material_difference": "The reviewer cannot confidently distinguish the claims as sufficiently different."}
        with patch("scripts.publish_axiom_thought.call", return_value=verdict):
            with self.assertRaisesRegex(ValueError, "AXIOM_SEMANTIC_REVIEW_BLOCKED: UNCERTAIN"):
                semantic_review(candidate, [SEED])

    def test_reviewer_outage_fails_closed_after_fallback(self) -> None:
        candidate = {"theme": "new-topic", "pl": CURRENT["pl"], "en": CURRENT["en"]}
        with patch("scripts.publish_axiom_thought.call", side_effect=RuntimeError("offline")) as mock_call:
            with self.assertRaisesRegex(RuntimeError, "unanimous two-model proof absent"):
                semantic_review(candidate, [SEED])
        self.assertEqual(mock_call.call_count, 2)

    def test_reviewer_invalid_reference_blocks_even_when_other_model_approves(self) -> None:
        candidate = {"theme": "new-topic", "pl": CURRENT["pl"], "en": CURRENT["en"]}
        invalid = {"verdict": "UNIQUE", "closest_ref": "not-in-archive",
                   "shared_claim": "Both discuss meaningful constraints on knowledge and decisions.",
                   "material_difference": "Their distinct approaches address different mechanisms and results."}
        valid = dict(invalid, closest_ref="2026-09-15")
        with patch("scripts.publish_axiom_thought.call", side_effect=[invalid, valid]) as mock_call:
            with self.assertRaisesRegex(RuntimeError, "unanimous two-model proof absent"):
                semantic_review(candidate, [SEED])
        self.assertEqual(mock_call.call_count, 2)


    def test_one_model_cannot_overrule_second_duplicate_verdict(self) -> None:
        candidate = {"theme": "friction-and-reality",
                     "pl": "„Usuwanie oporu może ukryć, czy nadal kontrolujemy rzeczywistość.”",
                     "en": "“Removing resistance can hide whether we still control reality.”"}
        earlier = dict(SEED, date="2026-09-20", theme="convenience-and-capability",
                       pl="„Wygoda potrafi odebrać nam umiejętności, które tworzyły niezależność.”",
                       en="“Convenience can remove the very skills that made us independent.”")
        unique = {"verdict": "UNIQUE", "closest_ref": "2026-09-20",
                  "shared_claim": "Both concern the hidden losses caused by removing everyday constraints.",
                  "material_difference": "One refers to feedback while the other refers to learned independent abilities."}
        veto = {"verdict": "DUPLICATE", "closest_ref": "2026-09-20",
                "shared_claim": "Both portray convenience and removal of friction as hiding the cost of lost agency.",
                "material_difference": "The same warning about dependence is restated through different metaphors."}
        with patch("scripts.publish_axiom_thought.call", side_effect=[unique, veto]) as mock_call:
            with self.assertRaisesRegex(ValueError, "DUPLICATE.*2026-09-20"):
                semantic_review(candidate, [earlier])
        self.assertEqual(mock_call.call_count, 2)

    def test_publication_requires_two_bound_review_receipts_from_october_12(self) -> None:
        prior_dates = {"2026-09-15", "2026-09-16"}
        candidate = {"theme": "new-causal-insight",
                     "pl": "„W nowych modelach przyczyna może być niewidoczna nawet wtedy, gdy skutek jest całkowicie przewidywalny.”",
                     "en": "“In new models the cause may be invisible even when its effect is entirely predictable.”"}
        approved = {"date": "2026-10-12", **candidate, "semantic_review_digest": review_digest(candidate),
                    "semantic_reviews": [
                        {"model": "reviewer-one", "verdict": "UNIQUE", "closest_ref": "2026-09-15",
                         "shared_claim": "Both describe the limits of insight in future choices and decisions.",
                         "material_difference": "This statement concerns causal identification rather than personal agency."},
                        {"model": "reviewer-two", "verdict": "UNIQUE", "closest_ref": "2026-09-16",
                         "shared_claim": "Both discuss limitations of knowledge in interpreting what seems understood.",
                         "material_difference": "The new thesis is about prediction without a verified explanatory model."}
                    ]}
        self.assertEqual(review_evidence_errors(approved, prior_dates), [])
        mutated = copy.deepcopy(approved)
        mutated["pl"] += " Nowe zdanie."
        self.assertTrue(any("not bound" in x for x in review_evidence_errors(mutated, prior_dates)))
        no_review = copy.deepcopy(approved)
        del no_review["semantic_reviews"]
        self.assertTrue(any("two independent" in x for x in review_evidence_errors(no_review, prior_dates)))
        same_model = copy.deepcopy(approved)
        same_model["semantic_reviews"][1]["model"] = "reviewer-one"
        self.assertTrue(any("distinct model" in x for x in review_evidence_errors(same_model, prior_dates)))
        current = {"date": approved["date"], "author": "AXIOM", "brand": "BriefRooms",
                   "pl": candidate["pl"], "en": candidate["en"]}
        dated = [copy.deepcopy(SEED), copy.deepcopy(LATEST), {
            **approved, "candidates_considered": 10, "scores": {"depth": 9, "novelty": 9, "banality_risk": 1},
            "silence_test": True,
            "editor_note": "A new causal thesis about observability and correct predictive models with a distinct epistemic mechanism.",
        }]
        self.assertEqual(validate(current, dated), [])
        dated[-1].pop("semantic_reviews")
        self.assertTrue(any("two independent semantic" in x for x in validate(current, dated)))

    def test_reserve_english_language_repeat_is_blocked(self) -> None:
        candidate = {"theme": "different-topic",
                     "pl": "„Zmiana perspektywy może odsłonić cechy systemu, których nie sposób dostrzec przy bezpośredniej obserwacji.”",
                     "en": LATEST["en"], "candidates_considered": 10,
                     "scores": {"depth": 9, "novelty": 9, "banality_risk": 1},
                     "silence_test": True,
                     "editor_note": "A different approach that should still be rejected because its English translation repeats a reserved line."}
        with self.assertRaisesRegex(ValueError, "too similar to reserve"):
            validate_candidate(candidate, [SEED], [LATEST])


    def test_live_smoke_checks_rejected_paraphrase_and_unique_reserve_without_writing(self) -> None:
        reserve = copy.deepcopy(LATEST)
        reserve.pop("date")
        mock_effects = [
            ValueError("AXIOM_SEMANTIC_REVIEW_BLOCKED: DUPLICATE closest=2026-09-15"),
            {"verdict": "UNIQUE"},
        ]
        with patch("scripts.publish_axiom_thought.semantic_review", side_effect=mock_effects) as review:
            self.assertEqual(semantic_smoke([SEED], [reserve]), 0)
        self.assertEqual(review.call_count, 2)

    def test_live_smoke_fails_when_duplicate_is_approved(self) -> None:
        reserve = copy.deepcopy(LATEST)
        reserve.pop("date")
        with patch("scripts.publish_axiom_thought.semantic_review", return_value={"verdict": "UNIQUE"}) as review:
            self.assertEqual(semantic_smoke([SEED], [reserve]), 2)
        review.assert_called_once()


    def test_publisher_rolls_back_both_files_if_final_guard_fails(self) -> None:
        with TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.jsonl"
            current_path = Path(directory) / "thought.json"
            original_history = '{"date":"2026-09-15"}\n'
            original_current = '{"date":"2026-09-15","author":"AXIOM"}\n'
            history_path.write_text(original_history, encoding="utf-8")
            current_path.write_text(original_current, encoding="utf-8")
            candidate = copy.deepcopy(LATEST)
            candidate.pop("date")
            candidate["theme"] = "predictability-and-surprise"
            candidate["pl"] = "„Jeżeli maszyna potrafi przewidzieć wszystkie nasze ruchy, największą niewiadomą przestaje być przyszłość, a staje się to, czy jeszcze umiemy ją zaskoczyć.”"
            candidate["en"] = "“If a machine can predict every move we make, the great unknown ceases to be the future and becomes whether we can still surprise it.”"
            with (
                patch("scripts.publish_axiom_thought.HISTORY", history_path),
                patch("scripts.publish_axiom_thought.CURRENT", current_path),
                patch("scripts.publish_axiom_thought.today", return_value="2026-09-17"),
                patch("scripts.publish_axiom_thought.validate_candidate"),
                patch("scripts.publish_axiom_thought.semantic_review", return_value=[]),
                patch("scripts.publish_axiom_thought.attach_review", side_effect=lambda c, r: c),
                patch("scripts.publish_axiom_thought.subprocess.run") as process,
            ):
                process.return_value.returncode = 1
                with self.assertRaisesRegex(RuntimeError, "post-publication guard failed"):
                    publish(candidate, [SEED, LATEST], "reserve")
            self.assertEqual(history_path.read_text(encoding="utf-8"), original_history)
            self.assertEqual(current_path.read_text(encoding="utf-8"), original_current)

    def test_publisher_never_changes_files_on_semantic_veto(self) -> None:
        with TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.jsonl"
            current_path = Path(directory) / "thought.json"
            history_path.write_text("original history", encoding="utf-8")
            current_path.write_text("original current", encoding="utf-8")
            candidate = copy.deepcopy(LATEST)
            candidate.pop("date")
            with (
                patch("scripts.publish_axiom_thought.HISTORY", history_path),
                patch("scripts.publish_axiom_thought.CURRENT", current_path),
                patch("scripts.publish_axiom_thought.validate_candidate"),
                patch("scripts.publish_axiom_thought.semantic_review", side_effect=ValueError("DUPLICATE")),
            ):
                with self.assertRaisesRegex(ValueError, "DUPLICATE"):
                    publish(candidate, [SEED, LATEST], "reserve")
            self.assertEqual(history_path.read_text(encoding="utf-8"), "original history")
            self.assertEqual(current_path.read_text(encoding="utf-8"), "original current")

    def test_temporary_semantic_outage_keeps_reserved_candidate(self) -> None:
        candidate = copy.deepcopy(LATEST)
        candidate.pop("date")
        with (
            patch("sys.argv", ["publish_axiom_thought.py", "--mode", "publish"]),
            patch("scripts.publish_axiom_thought.today", return_value="2026-09-17"),
            patch("scripts.publish_axiom_thought.load_history", return_value=[SEED, LATEST]),
            patch("scripts.publish_axiom_thought.load_reserve", return_value=[candidate]),
            patch("scripts.publish_axiom_thought.publish", side_effect=RuntimeError("AI offline")),
            patch("scripts.publish_axiom_thought.save_reserve") as save,
        ):
            self.assertEqual(main(), 2)
        save.assert_not_called()

    def test_live_batch_has_ten_explicit_distinct_candidates_before_acceptance(self) -> None:
        template = copy.deepcopy(LATEST)
        template.pop("date")
        candidates = [{**template,
                       "theme": f"idea-{i}",
                       "pl": f"„Odważna refleksja numer {i} odkrywa odmienny mechanizm poznawczy, którego przyczyny i skutki należy rozważyć.”"}
                      for i in range(10)]
        with (
            patch("scripts.publish_axiom_thought.call", return_value={"candidates": candidates}) as api,
            patch("scripts.publish_axiom_thought.validate_candidate"),
            patch("scripts.publish_axiom_thought.attach_review", side_effect=lambda c, r, **kw: {"approved": c["theme"]}),
        ):
            chosen = generate_ten([SEED], [])
        self.assertEqual(chosen, {"approved": "idea-0"})
        api.assert_called_once()

    def test_failed_live_batch_consumes_verified_reserve_without_blocking_day(self) -> None:
        candidate = copy.deepcopy(LATEST)
        candidate.pop("date")
        with (
            patch("sys.argv", ["publish_axiom_thought.py", "--mode", "publish"]),
            patch("scripts.publish_axiom_thought.today", return_value="2026-09-17"),
            patch("scripts.publish_axiom_thought.load_history", return_value=[SEED, LATEST]),
            patch("scripts.publish_axiom_thought.load_reserve", return_value=[candidate]),
            patch("scripts.publish_axiom_thought.generate_ten", side_effect=RuntimeError("all ten rejected")),
            patch("scripts.publish_axiom_thought.evidence_valid", return_value=True),
            patch("scripts.publish_axiom_thought.publish") as publish_mock,
            patch("scripts.publish_axiom_thought.save_reserve") as saved,
        ):
            self.assertEqual(main(), 0)
        self.assertEqual(publish_mock.call_count, 1)
        self.assertEqual(publish_mock.call_args.args[-1], "reserve-vetted")
        saved.assert_called_once_with([])

    def test_reserve_capacity_is_100_and_fill_is_bounded_per_run(self) -> None:
        from scripts import publish_axiom_thought as publisher
        self.assertEqual(publisher.RESERVE_TARGET, 100)
        rows = [SEED, LATEST]
        stored = []
        generated = copy.deepcopy(LATEST)
        generated.pop("date")
        with (
            patch("scripts.publish_axiom_thought.REFILL_BATCH", 2),
            patch("scripts.publish_axiom_thought.generate", return_value=generated) as gen,
            patch("scripts.publish_axiom_thought.save_reserve", side_effect=lambda x: stored.append(len(x))),
            patch("scripts.publish_axiom_thought.evidence_valid", return_value=True),
        ):
            pool = []
            self.assertTrue(refill(rows, pool))
        self.assertEqual(len(pool), 2)
        self.assertEqual(gen.call_count, 2)
        self.assertEqual(stored, [1, 2])

    def test_unvetted_reserve_is_never_misrepresented_as_preapproved(self) -> None:
        candidate = copy.deepcopy(LATEST)
        candidate.pop("date")
        self.assertFalse(evidence_valid(candidate, [SEED]))
        self.assertEqual(core_candidate(candidate), candidate)

    def test_heartbeat_is_parseable_json_and_generated_with_json_serializer(self) -> None:
        root = Path(__file__).resolve().parents[1]
        heartbeat = json.loads((root / "data/home/axiom-thought-heartbeat.json").read_text(encoding="utf-8"))
        self.assertEqual(set(heartbeat), {"date", "event", "run_id", "sha"})
        self.assertIsInstance(date.fromisoformat(heartbeat["date"]), date)
        workflow = (root / ".github/workflows/axiom-thought-daily.yml").read_text(encoding="utf-8")
        self.assertIn('json.dumps(data, ensure_ascii=False', workflow)
        self.assertNotIn("printf ", workflow)

    def test_recovery_scheduler_is_independent_and_serialized(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/axiom-thought-daily.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", workflow)
        self.assertIn('"Production Investment Event Intelligence"', workflow)
        self.assertIn('"BRs R2 Shadow Archive Pilot"', workflow)
        self.assertIn("group: morning-axiom-daily\n", workflow)
        self.assertIn("continue-on-error: true", workflow)


    def test_rhetorical_templates_detect_repeated_constructions(self) -> None:
        self.assertIn("rarely-frame", rhetorical_templates(SEED["pl"]))
        self.assertIn("rarely-more-often", rhetorical_templates(SEED["pl"]))
        self.assertIn("not-when-but", rhetorical_templates(
            "„Prawdziwa przewaga pojawia się nie wtedy, gdy wygrywamy, lecz gdy zmieniamy reguły.”"))
        self.assertIn("superlative-opening", rhetorical_templates(
            "„Najtrudniejsza decyzja dotyczy tego, czego jeszcze nie potrafimy nazwać.”"))
        self.assertNotIn("rarely-frame", rhetorical_templates(
            "„Granice mapy pozostają niewidoczne dla obserwatora, który nigdy z niej nie wychodzi.”"))

    def test_prospective_guard_softly_scores_same_rhetoric_without_rejecting_idea(self) -> None:
        earlier = dict(SEED, date="2026-10-12", theme="competition-and-mimicry")
        newer = dict(LATEST, date="2026-10-13", theme="archive-and-attention",
                     pl="„Archiwum rzadko chroni wyłącznie przeszłość — częściej uczy przyszłych badaczy, jakie pytania warto przemilczeć.”",
                     en="“An archive rarely protects only the past; more often it teaches future researchers which questions to leave unspoken.”")
        current = {"date": newer["date"], "author": "AXIOM", "brand": "BriefRooms",
                   "pl": newer["pl"], "en": newer["en"]}
        errors = validate(current, [earlier, newer], require_review_evidence=False)
        self.assertFalse(any("rhetorical-style" in error for error in errors), errors)
        self.assertLess(rhetorical_diversity_score(newer["pl"], [earlier], "2026-10-13"), 10)

    def test_existing_october_12_publication_is_grandfathered(self) -> None:
        earlier = dict(SEED, date="2026-10-09", theme="permanence-and-maintenance")
        current_record = dict(LATEST, date="2026-10-12", theme="competition-and-mimicry",
                              pl="„Długotrwała rywalizacja rzadko wyłania unikalnego zwycięzcę — częściej upodabnia do siebie konkurentów tak skutecznie, że ostateczna różnica między nimi staje się wyłącznie kwestią retoryki.”",
                              en="“Long-term competition rarely produces a unique winner — competitors converge until differences become rhetorical.”")
        current = {"date": current_record["date"], "author": "AXIOM", "brand": "BriefRooms",
                   "pl": current_record["pl"], "en": current_record["en"]}
        errors = validate(current, [earlier, current_record], require_review_evidence=False)
        self.assertFalse(any("rhetorical-style" in error for error in errors), errors)

    def test_rhetorical_rotation_expires_but_conceptual_dedup_does_not(self) -> None:
        history = [dict(SEED, date="2026-09-12")]
        self.assertEqual(rhetorical_style_issues(
            "„System rzadko szuka prawdy, bo częściej nagradza zgodność.”",
            history, "2026-10-13"), [])
        self.assertTrue(rhetorical_style_issues(
            "„System rzadko szuka prawdy, bo częściej nagradza zgodność.”",
            history, "2026-10-12") == [])

    def test_superlative_opening_is_rotated_independently(self) -> None:
        prior = dict(SEED, date="2026-10-10",
                     pl="„Najważniejszy sygnał ostrzegawczy może pojawić się tam, gdzie milczą wszystkie wskaźniki.”")
        candidate = "„Najgłębszym kosztem porozumienia może być stopniowa utrata możliwości zadawania niewygodnych pytań.”"
        issues = rhetorical_style_issues(candidate, [prior], "2026-10-13")
        self.assertTrue(any("superlative-opening" in issue for issue in issues), issues)
        self.assertFalse(rhetorical_style_issues(candidate, [prior], "2026-10-25"))

    def test_reserve_candidate_may_repeat_rhetoric_with_new_idea(self) -> None:
        candidate = dict(LATEST)
        candidate.pop("date")
        candidate["theme"] = "new-causal-theme"
        candidate["pl"] = "„Długotrwały spór rzadko ujawnia sedno problemu — częściej prowadzi do odkrycia nowych błędów organizacyjnych.”"
        candidate["en"] = "“A prolonged dispute rarely reveals the heart of a problem, but often exposes new organizational failures.”"
        with patch("scripts.publish_axiom_thought.today", return_value="2026-10-13"):
            validate_candidate(candidate, [SEED], [])

    def test_canonical_archive_remains_valid_with_prospective_style_guard(self) -> None:
        root = Path(__file__).resolve().parents[1]
        rows = [json.loads(x) for x in (root / "data/home/axiom-thoughts-history.jsonl")
                .read_text(encoding="utf-8").splitlines() if x.strip()]
        current = json.loads((root / "data/home/axiom-thought.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(current, rows), [])
        self.assertEqual(rows[0]["date"], "2026-09-15")
        self.assertEqual(rows[-1]["date"], current["date"])

    def test_hourly_refill_preserves_verified_bank_during_provider_outage(self) -> None:
        reserve = [dict(LATEST, theme="old-template",
                        pl="„Uznanie rzadko tworzy nową odpowiedzialność — częściej odsłania wcześniejsze oczekiwania wobec władzy.”",
                        en="“Recognition rarely creates a new responsibility; more often, it reveals prior expectations about authority.”")]
        reserve[0].pop("date")
        saved = []
        with (
            patch("scripts.publish_axiom_thought.today", return_value="2026-10-13"),
            patch("scripts.publish_axiom_thought.evidence_valid", return_value=True),
            patch("scripts.publish_axiom_thought.generate", side_effect=RuntimeError("offline")),
            patch("scripts.publish_axiom_thought.save_reserve", side_effect=lambda x: saved.append(len(x))),
        ):
            self.assertTrue(refill([SEED], reserve))
        self.assertEqual(len(reserve), 1)
        self.assertEqual(saved, [])


    def test_verified_reserve_health_requires_ten_real_distinct_candidates(self) -> None:
        rows = [SEED]
        candidates = [
            {**LATEST, "theme": f"distinct-theme-{index}",
             "pl": f"„Oryginalny koncept numer {index} zmienia sposób rozumienia systemu, a konsekwencja poznawcza jest istotna dla niezależnej decyzji.”"}
            for index in range(10)
        ]
        for candidate in candidates:
            candidate.pop("date", None)
        with (patch("scripts.publish_axiom_thought.evidence_valid", return_value=True),
              patch("scripts.publish_axiom_thought.validate_candidate")):
            self.assertFalse(reserve_status(rows, candidates[:9]))
            self.assertTrue(reserve_status(rows, candidates))
            self.assertEqual(len(eligible_reserve(rows, candidates)), 10)
        with patch("scripts.publish_axiom_thought.evidence_valid", return_value=False):
            self.assertFalse(reserve_status(rows, candidates))

    def test_repeat_theme_is_still_rejected_despite_soft_rhetoric(self) -> None:
        candidate = copy.deepcopy(LATEST)
        candidate.pop("date")
        candidate["theme"] = SEED["theme"]
        with patch("scripts.publish_axiom_thought.today", return_value="2026-10-16"):
            with self.assertRaisesRegex(ValueError, "theme"):
                validate_candidate(candidate, [SEED], [])

    def test_canonical_recovered_reserve_only_counts_reviewed_original_items(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bank = json.loads((root / "data/home/axiom-thought-reserve.json").read_text(encoding="utf-8"))
        rows = [json.loads(row) for row in (root / "data/home/axiom-thoughts-history.jsonl")
                .read_text(encoding="utf-8").splitlines() if row.strip()]
        self.assertGreaterEqual(len(bank), 7)
        self.assertTrue(all(evidence_valid(candidate, rows) for candidate in bank[:7]))
        self.assertGreaterEqual(len(eligible_reserve(rows, bank)), 7)


if __name__ == "__main__":
    unittest.main()
