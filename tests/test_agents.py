
import json
import unittest

from agents import diagnose_incident


GOOD_RESPONSE = {
    "root_cause": "Simulated CPU saturation",
    "evidence": ["CPU utilization reached 95%"],
    "confidence_score": 87,
    "risk_level": "medium",
    "blast_radius": {
        "affected_components": ["simulated_worker"],
        "impact": "Increased response latency",
    },
    "recovery_steps": [
        {
            "action_id": "stop_owned_chaos_worker",
            "parameters": {},
        }
    ],
    "rollback_conditions": ["Stop recovery if CPU does not improve"],
}


class TestAIAgents(unittest.TestCase):
    def setUp(self):
        self.metrics = {
            "cpu_percent": 95,
            "ram_percent": 70,
            "tps": 120,
            "lock_count": 1150,
        }

    def test_successful_diagnosis(self):
        def fake_model(prompt, timeout):
            return json.dumps(GOOD_RESPONSE)

        result = diagnose_incident(self.metrics, fake_model)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["root_cause"], "Simulated CPU saturation")
        self.assertTrue(result["ai_generated"])

    def test_malformed_json_returns_fallback(self):
        def fake_model(prompt, timeout):
            return "{invalid json"

        result = diagnose_incident(
            self.metrics, fake_model, max_attempts=1
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["recovery_steps"], [])
        self.assertFalse(result["ai_generated"])

    def test_timeout_returns_fallback(self):
        def fake_model(prompt, timeout):
            raise TimeoutError("Simulated timeout")

        result = diagnose_incident(
            self.metrics, fake_model, max_attempts=2
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["recovery_steps"], [])
        self.assertEqual(result["risk_level"], "high")

    def test_unsafe_action_is_rejected(self):
        unsafe_response = dict(GOOD_RESPONSE)
        unsafe_response["recovery_steps"] = [
            {
                "action_id": "run_arbitrary_shell_command",
                "parameters": {},
            }
        ]

        def fake_model(prompt, timeout):
            return json.dumps(unsafe_response)

        result = diagnose_incident(
            self.metrics, fake_model, max_attempts=1
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["recovery_steps"], [])

    def test_missing_field_returns_fallback(self):
        incomplete = dict(GOOD_RESPONSE)
        del incomplete["rollback_conditions"]

        def fake_model(prompt, timeout):
            return json.dumps(incomplete)

        result = diagnose_incident(
            self.metrics, fake_model, max_attempts=1
        )

        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
