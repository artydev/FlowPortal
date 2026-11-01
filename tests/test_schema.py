"""Tests unitaires de schema : le validateur des workflows."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import schema  # noqa: E402


def errors(issues):
    return [m for lvl, _, m in issues if lvl == "error"]


class CheckValidTests(unittest.TestCase):

    def test_all_project_workflows_are_valid(self):
        for p in sorted(Path(ROOT, "workflows").glob("*.yaml")):
            with self.subTest(workflow=p.name):
                issues = schema.check(p.read_text(encoding="utf-8"))
                self.assertEqual(errors(issues), [], f"erreurs dans {p.name} : {issues}")


class CheckErrorDetectionTests(unittest.TestCase):

    def test_unknown_node_type_is_error(self):
        text = "name: t\nsteps:\n  - id: a\n    type: inexistant\n"
        self.assertTrue(errors(schema.check(text)))

    def test_duplicate_id_is_error(self):
        text = ("name: t\nsteps:\n"
                "  - id: a\n    type: log\n    message: x\n"
                "  - id: a\n    type: log\n    message: y\n")
        self.assertTrue(errors(schema.check(text)))

    def test_missing_steps_is_error(self):
        self.assertTrue(errors(schema.check("name: t\n")))

    def test_invalid_yaml_is_error(self):
        self.assertTrue(errors(schema.check("steps: [\n  - id: a\n   type: log")))

    def test_unknown_top_key_is_warn(self):
        text = "name: t\nfoo: 1\nsteps:\n  - id: a\n    type: log\n    message: x\n"
        issues = schema.check(text)
        self.assertTrue(any(lvl == "warn" for lvl, _, _ in issues))

    def test_unknown_reference_is_detected(self):
        text = ("name: t\nsteps:\n"
                "  - id: a\n    type: log\n    message: '{{ref_inconnue.output}}'\n")
        self.assertTrue(schema.check(text))

    def test_known_inputs_are_allowed(self):
        text = ("name: t\ninputs:\n  projet: tmp/x\nsteps:\n"
                "  - id: a\n    type: file_write\n    path: '{{projet}}/f'\n    content: c\n")
        self.assertEqual(errors(schema.check(text)), [])


class JsonSchemaTests(unittest.TestCase):

    def test_json_schema_contains_steps(self):
        s = schema.json_schema()
        if isinstance(s, str):
            s = json.loads(s)
        props = s.get("properties", {})
        self.assertIn("steps", props)
        self.assertIn("name", props)


if __name__ == "__main__":
    unittest.main()
