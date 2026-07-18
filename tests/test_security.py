import unittest

from revenue_integrity.security import ModelAccessPolicy, RetentionPolicy


class SecurityPolicyTests(unittest.TestCase):
    def test_model_access_fails_closed(self):
        policy = ModelAccessPolicy(("provider-a",))
        policy.authorize(provider="provider-a", deidentified=True, input_characters=100, zero_retention=True, telemetry_enabled=False)
        with self.assertRaises(PermissionError):
            policy.authorize(provider="provider-b", deidentified=True, input_characters=100, zero_retention=True, telemetry_enabled=False)
        with self.assertRaises(PermissionError):
            policy.authorize(provider="provider-a", deidentified=False, input_characters=100, zero_retention=True, telemetry_enabled=False)

    def test_retention_policy_rejects_raw_input_artifacts(self):
        with self.assertRaises(PermissionError):
            RetentionPolicy().validate_artifact({"contains_raw_input": True})
