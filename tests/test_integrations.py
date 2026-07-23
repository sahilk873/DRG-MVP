import unittest

from revenue_integrity.grouper import DeterministicDemoGrouper
from revenue_integrity.integrations import (
    CapabilityKind,
    CapabilityRegistry,
    EquivalenceTable,
    TableTerminologyService,
    UnavailableTerminologyService,
    load_equivalence_table,
)


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


class TableTerminologyServiceTests(unittest.TestCase):
    def test_known_synonym_folds_to_canonical(self):
        service = TableTerminologyService()
        self.assertEqual(service.normalize("dti"), "deep_tissue_pressure_injury")
        # case-insensitive, whitespace-tolerant match
        self.assertEqual(service.normalize("  Deep Tissue Injury "), "deep_tissue_pressure_injury")

    def test_unknown_value_passes_through_unchanged(self):
        service = TableTerminologyService()
        self.assertEqual(service.normalize("stage 3 sacral ulcer"), "stage 3 sacral ulcer")
        # an already-canonical value it does not index is passed through, never fabricated
        self.assertEqual(service.normalize("some_unmapped_code"), "some_unmapped_code")

    def test_table_digest_is_stable(self):
        first = load_equivalence_table().digest
        second = load_equivalence_table().digest
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        # digest is content-addressed: a changed mapping yields a different digest
        mutated = EquivalenceTable(
            table_id="wound-care-terms",
            version="1.0.0",
            status="approved-for-demo",
            equivalences={"dti": "something_else"},
        )
        self.assertNotEqual(mutated.digest, first)

    def test_service_descriptor_is_not_production_ready(self):
        service = TableTerminologyService()
        descriptor = service.descriptor
        self.assertEqual(descriptor.kind, CapabilityKind.TERMINOLOGY)
        self.assertFalse(descriptor.production_ready)

    def test_unapproved_status_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not approved"):
            EquivalenceTable(
                table_id="t",
                version="1",
                status="draft",
                equivalences={"a": "b"},
            )

    def test_conflicting_synonym_casing_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting canonical"):
            EquivalenceTable(
                table_id="t",
                version="1",
                status="approved-for-demo",
                equivalences={"DTI": "x", "dti": "y"},
            )


if __name__ == "__main__":
    unittest.main()
