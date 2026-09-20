"""Candidate coverage, alternative identity, evidence grounding and partial failure."""
import copy
import unittest

import fixtures
import development_report
import idea_development as development

TODAY = "2026-09-19"


class DevelopmentTests(unittest.TestCase):
    def run_pipeline(self, client=None, count=4, research_fn=fixtures.research, evidence_fn=lambda c: None):
        return development.run(client or fixtures.PipelineClient(), fixtures.merged_cards(count), TODAY,
                               research_fn=research_fn, evidence_fn=evidence_fn)

    def test_compares_every_candidate_before_selecting_three(self):
        def mutate(stage, rows):
            if stage == "assessments":
                rows[-1].update(demand=5, trend=5, gap=5)
        client = fixtures.PipelineClient(mutate=mutate)
        result = self.run_pipeline(client, count=15)
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["comparisons"]), 15)
        self.assertEqual(result["selected"][0], 14)
        self.assertEqual(len(result["selected"]), 3)
        self.assertEqual(len(result["variants"]), 9)
        self.assertEqual(len(result["plans"]), 2)
        parents = {result["variants"][i]["parent_i"] for i in result["winners"]}
        self.assertEqual(len(parents), 2)
        sent = client.calls[0]["payload"]["cards"]
        self.assertEqual(len(sent), 15)
        self.assertTrue(all("outlets" not in c and "chart_rank" not in c for c in sent))

    def test_missing_evidence_caps_scores_and_cannot_award_go(self):
        result = self.run_pipeline(research_fn=lambda *args: ([], []))
        self.assertTrue(result["complete"])
        for row in result["comparisons"] + result["reviews"]:
            self.assertLessEqual(row["demand"], 2)
            self.assertLessEqual(row["trend"], 1)
        self.assertTrue(all(row["verdict"] == "보류" for row in result["reviews"]))

    def test_unrelated_general_news_cannot_override_low_feasibility(self):
        def mutate(stage, rows):
            if stage == "reviews":
                for row in rows:
                    row["feasibility"] = 1
        result = self.run_pipeline(fixtures.PipelineClient(mutate=mutate))
        self.assertTrue(all(row["verdict"] != "GO" for row in result["reviews"]))

    def test_all_stop_never_runs_development(self):
        def mutate(stage, rows):
            if stage == "reviews":
                for row in rows:
                    row["verdict"] = "STOP"
        client = fixtures.PipelineClient(mutate=mutate)
        result = self.run_pipeline(client)
        self.assertTrue(result["complete"])
        self.assertEqual(result["plans"], [])
        self.assertEqual(result["winners"], [])
        self.assertEqual(len(client.calls), 3)

    def test_no_viable_candidate_stops_without_forcing_alternatives(self):
        def mutate(stage, rows):
            if stage == "assessments":
                for row in rows:
                    row["demand"] = 1
        client = fixtures.PipelineClient(mutate=mutate)
        result = self.run_pipeline(client)
        self.assertTrue(result["complete"])
        self.assertEqual(result["selected"], [])
        self.assertEqual(len(client.calls), 1)

    def test_empty_candidates_never_call_model_or_research(self):
        def unexpected(*args):
            raise AssertionError("unexpected research")
        result = development.run(None, [], TODAY, research_fn=unexpected)
        self.assertTrue(result["complete"])

    def test_research_and_competitor_failures_leave_explicit_warnings(self):
        def boom(*args):
            raise OSError("private-detail")
        result = self.run_pipeline(research_fn=boom, evidence_fn=boom)
        self.assertTrue(result["complete"])
        self.assertIn("수요·트렌드 자료 수집 실패", " ".join(result["warnings"]))
        self.assertIn("국내 유사 서비스 검색 실패", " ".join(result["warnings"]))
        self.assertNotIn("private-detail", str(result))

    def test_stage_failure_retains_only_completed_work(self):
        for failed, kept in (("assessments", []), ("variants", ["comparisons"]),
                             ("reviews", ["comparisons", "variants"]),
                             ("plans", ["comparisons", "variants", "reviews"])):
            with self.subTest(failed=failed):
                result = self.run_pipeline(fixtures.PipelineClient(fail=failed))
                self.assertFalse(result["complete"])
                self.assertTrue(result["failed_stage"])
                self.assertTrue(result["warnings"])
                for key in kept:
                    self.assertTrue(result[key])
                self.assertEqual(result["plans"], [])

    def test_invalid_ids_missing_rows_unknown_citations_and_nested_types_fail_closed(self):
        mutations = [lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                     lambda rows: rows[0].update(i=True), lambda rows: rows[0].update(i=999),
                     lambda rows: rows[0].update(evidence_ids=["S999"]),
                     lambda rows: rows[0].update(evidence_ids=["C0", "C0"]),
                     lambda rows: rows[0].update(demand=6), lambda rows: rows[0].update(pain="  "),
                     lambda rows: rows[0].update(unknowns=[9]),
                     lambda rows: rows[0].update(extra="unexpected")]
        for change in mutations:
            def mutate(stage, rows):
                if stage == "assessments":
                    change(rows)
            with self.subTest(change=change):
                result = self.run_pipeline(fixtures.PipelineClient(mutate=mutate))
                self.assertFalse(result["complete"])
                self.assertEqual(result["failed_stage"], "후보 비교")
                self.assertEqual(result["comparisons"], [])

    def test_three_labels_do_not_disguise_identical_pivots_or_wrong_parent(self):
        for kind in ("duplicate", "direction", "parent"):
            def mutate(stage, rows):
                if stage != "variants":
                    return
                if kind == "duplicate":
                    for key in ("target_users", "added_axis", "decision"):
                        rows[1][key] = rows[0][key]
                elif kind == "direction":
                    rows[1]["direction"] = rows[0]["direction"]
                else:
                    rows[0]["parent_i"] = 999
            with self.subTest(kind=kind):
                result = self.run_pipeline(fixtures.PipelineClient(mutate=mutate))
                self.assertEqual(result["failed_stage"], "피벗·파생")
                self.assertEqual(result["variants"], [])

    def test_final_plan_cannot_silently_replace_selected_variant(self):
        for key in ("i", "title", "target_users", "added_axis", "decision"):
            def mutate(stage, rows):
                if stage == "plans":
                    rows[0][key] = 999 if key == "i" else "다른 아이디어"
            with self.subTest(key=key):
                result = self.run_pipeline(fixtures.PipelineClient(mutate=mutate))
                self.assertEqual(result["failed_stage"], "최종안 심화")
                self.assertEqual(result["plans"], [])
                self.assertTrue(result["reviews"])

    def test_same_story_is_one_source_across_research_rounds(self):
        result = self.run_pipeline()
        urls = [source["url"] for source in result["sources"].values()]
        self.assertEqual(len(urls), len(set(urls)))

    def test_full_archive_includes_all_candidates_and_actionable_plan(self):
        result = self.run_pipeline(count=15)
        full = "\n".join(development_report.render(result, TODAY, full=True))
        compact = "\n".join(development_report.render(result, TODAY))
        self.assertIn("외 3개도 비교", compact)
        self.assertNotIn("외 3개도 비교", full)
        for token in ("15.", "반증 조건", "반론", "다른 안과 비교", "첫 10명 모집", "1주차", "2주차",
                      "중단 기준(제안)", "실패 시 다음 피벗", "https://example.com/trend", "2026-09-19"):
            self.assertIn(token, full)


if __name__ == "__main__":
    unittest.main()
