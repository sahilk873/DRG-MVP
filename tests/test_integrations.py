import unittest

from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.integrations import CapabilityKind, CapabilityRegistry, UnavailableTerminologyService


class IntegrationCapabilityTests(unittest.TestCase):
    def test_production_registry_rejects_demo_components(self):
        registry = CapabilityRegistry(production=True)
        with self.assertRaisesRegex(ValueError, "not approved"):
            registry.register(DeterministicDemoGrouper())

    def test_registry_resolves_explicit_nonproduction_capability(self):
        registry = CapabilityRegistry()
        component = UnavailableTerminologyService()
        registry.register(component)
        self.assertIs(registry.resolve(CapabilityKind.TERMINOLOGY, "terminology-unavailable"), component)


if __name__ == "__main__":
    unittest.main()
