from pathlib import Path
import json
import unittest

from koali_control.levelupdiag import DEFAULT_CAMPAIGNS, LevelUpDiagAdapter

ROOT = Path(__file__).resolve().parents[1]


class StabilizationWorkflowTests(unittest.TestCase):
    def test_default_workflow_focus_is_core_stabilization(self):
        cfg = json.loads((ROOT / "koali-control.json").read_text(encoding="utf-8"))
        workflow = cfg["workflow"]
        self.assertEqual(workflow["current_focus"], "core_stabilization")
        self.assertNotIn("phase", workflow)
        self.assertEqual(workflow["qualification_scope"], "koali_core_pre_subsystem")
        self.assertEqual(workflow["final_profile"], "sovereign-linux-node")
        self.assertEqual(workflow["external_subsystems"]["konnaxion"], "placeholder_until_integration")
        self.assertEqual(workflow["external_subsystems"]["ariane"], "deferred_until_koali_integration_test")
        self.assertEqual(workflow["external_subsystems"]["orgo"], "draft_not_admitted")
        self.assertEqual(workflow["external_subsystems"]["semantik_architect"], "deferred_not_admitted")


    def test_v4_dev_stack_keeps_products_modular(self):
        cfg = json.loads((ROOT / "koali-control.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["schema_version"], 4)
        self.assertEqual(cfg["dev_stack"]["products"], ["konnaxion", "koali-spaces"])
        self.assertFalse(cfg["products"]["konnaxion"]["optional"])
        self.assertFalse(cfg["products"]["koali-spaces"]["optional"])
        self.assertTrue(cfg["products"]["orgo"]["optional"])
        self.assertFalse(cfg["products"]["orgo"]["enabled"])

    def test_control_panel_campaign_mapping_matches_levelupdiag_22(self):
        cfg = json.loads((ROOT / "koali-control.json").read_text(encoding="utf-8"))
        campaigns = cfg["diagnostics"]["levelupdiag"]["campaigns"]
        self.assertEqual(campaigns["stabilization"], "stabilization")
        self.assertEqual(campaigns["stabilization_runtime"], "stabilization-runtime")
        self.assertEqual(campaigns["run_all"], "validation")
        self.assertEqual(campaigns["release"], "release")
        self.assertEqual(DEFAULT_CAMPAIGNS["build"], "stabilization")

    def test_ui_exposes_core_first_actions_and_preserves_final_assembly(self):
        source = (ROOT / "koali_control" / "app.py").read_text(encoding="utf-8")
        self.assertIn('"STABILIZE KOALI CORE"', source)
        self.assertIn('"CORE STABILITY CHECK"', source)
        self.assertIn('"CORE RUNTIME DIAGNOSTICS"', source)
        self.assertIn('"FINAL PROFILE ASSEMBLY"', source)
        self.assertIn("External subsystem admission is deferred, never fabricated", source)

    def test_final_build_selection_does_not_persist_over_development_profile(self):
        source = (ROOT / "koali_control" / "app.py").read_text(encoding="utf-8")
        sync = source[source.index("def _sync_build_to_workspace"):source.index("def assemble", source.index("def _sync_build_to_workspace"))]
        self.assertIn("Workspace.from_config(base.workspace_id, base.to_config())", sync)
        self.assertNotIn('self.config_data["workspaces"]', sync)

    def test_levelupdiag_summary_presentation_exposes_scope_without_remapping_verdict(self):
        text = LevelUpDiagAdapter._format_report({
            "schema": "levelupdiag.campaign-summary.v2",
            "selection": "stabilization",
            "qualification_scope": "koali_core_pre_subsystem",
            "deferred_subsystems": ["konnaxion", "ariane", "orgo", "semantik_architect"],
            "verdict": "WARN",
            "levels": [{"id": "N01", "name": "Environment", "verdict": "WARN", "findings": []}],
        })
        self.assertIn("LevelUpDiag campaign — stabilization: WARN", text)
        self.assertIn("Qualification scope: koali_core_pre_subsystem", text)
        self.assertIn("Deferred subsystems: konnaxion, ariane, orgo, semantik_architect", text)
        self.assertIn("N01 — Environment: WARN", text)


if __name__ == "__main__":
    unittest.main()
