import unittest

from revenue_integrity.runtime import (
    ArtifactScore,
    DEFAULT_TENANT_ID,
    DeterministicRetriever,
    Exemplar,
    KnowledgeStore,
    admit_artifact,
    estimate_tokens,
    learn_from_decision,
)
from revenue_integrity.runtime.retrieval import retrieval_pack

CLINIC_ALPHA_COLUMNS = [
    "case_id", "patient_id", "encounter_id", "admitted_at", "discharged_at", "facility",
    "note_id", "author_role", "note_text", "submitted_drg", "allowed_amount_cents",
    "diagnosis_code", "charge_code", "assessment_id", "wound_id", "stage", "site", "poa",
]
MERCY_COLUMNS = [
    "case_id", "mrn", "encounter_id", "admit_dttm", "disch_dttm", "facility", "final_drg",
    "expected_reimb_cents", "icd10_cm", "rev_code", "assess_id", "wound_num", "pi_stage",
    "body_site", "poa_flag",
]


def _adapter_exemplar(exemplar_id: str, columns: list[str], adapter_id: str) -> Exemplar:
    return Exemplar(
        exemplar_id=exemplar_id, kind="adapter_mapping", features=columns,
        payload={"adapter_id": adapter_id}, label="approved",
        provenance={"adapter_id": adapter_id},
    )


class KnowledgeStoreTests(unittest.TestCase):
    def test_records_chain_and_verify(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("a", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        store.record(_adapter_exemplar("b", MERCY_COLUMNS, "mercy-regional"))
        self.assertEqual(len(store), 2)
        self.assertTrue(store.verify_chain())

    def test_recording_is_idempotent_on_content(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("a", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        store.record(_adapter_exemplar("a-again", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        self.assertEqual(len(store), 1)  # identical content -> single ledger entry

    def test_roundtrip_and_digest_are_deterministic(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("a", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        restored = KnowledgeStore.from_dict(store.to_dict())
        self.assertEqual(restored.digest, store.digest)
        self.assertTrue(restored.verify_chain())

    def test_tamper_breaks_chain(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("a", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        store._records_by_tenant[DEFAULT_TENANT_ID][0]["content_hash"] = "0" * 64
        self.assertFalse(store.verify_chain())


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore()
        self.store.record(_adapter_exemplar("clinic", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        self.store.record(_adapter_exemplar("mercy", MERCY_COLUMNS, "mercy-regional"))
        self.retriever = DeterministicRetriever(self.store)

    def test_retrieves_the_most_similar_prior_mapping(self):
        # A new provider whose columns resemble the Mercy export should surface Mercy as precedent.
        query = ["mrn", "encounter_id", "admit_dttm", "disch_dttm", "icd10_cm", "pi_stage", "body_site", "poa_flag"]
        result = self.retriever.retrieve(query, kind="adapter_mapping", k=1)
        self.assertEqual(result.exemplars[0].payload["adapter_id"], "mercy-regional")

    def test_retrieval_is_deterministic_and_digest_stable(self):
        query = ["mrn", "encounter_id", "icd10_cm", "pi_stage"]
        a = self.retriever.retrieve(query, kind="adapter_mapping", k=2)
        b = self.retriever.retrieve(query, kind="adapter_mapping", k=2)
        self.assertEqual(a.retrieval_digest, b.retrieval_digest)
        self.assertEqual([e.exemplar_id for e in a.exemplars], [e.exemplar_id for e in b.exemplars])

    def test_k_bounds_results_and_min_overlap_filters(self):
        result = self.retriever.retrieve(["totally", "unrelated", "columns"], kind="adapter_mapping", k=5)
        self.assertEqual(result.exemplars, ())  # no feature overlap -> nothing retrieved

    def test_rag_pack_is_far_smaller_than_dumping_everything(self):
        # Seed a large store; RAG should inject only the top-k, not the whole memory.
        for index in range(200):
            self.store.record(Exemplar(
                exemplar_id=f"x{index}", kind="adapter_mapping",
                features=[f"col_{index}_{j}" for j in range(12)], payload={"adapter_id": f"a{index}"}, label="approved",
            ))
        query = ["mrn", "encounter_id", "admit_dttm", "icd10_cm", "pi_stage", "poa_flag"]
        pack = retrieval_pack(self.retriever.retrieve(query, kind="adapter_mapping", k=2))
        everything = [e.to_dict() for e in self.store.exemplars("adapter_mapping")]
        self.assertLess(estimate_tokens(pack), estimate_tokens(everything) // 10)


class SelfLearningTests(unittest.TestCase):
    def test_store_improves_as_verified_experience_accumulates(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("clinic", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        retriever = DeterministicRetriever(store)
        # "facility" overlaps the clinic mapping; the rest resembles a Mercy-shaped export.
        query = ["facility", "mrn", "admit_dttm", "disch_dttm", "icd10_cm", "rev_code", "pi_stage", "body_site", "poa_flag"]

        before = retriever.retrieve(query, kind="adapter_mapping", k=1)
        self.assertEqual(before.exemplars[0].payload["adapter_id"], "clinic-alpha")

        # The system "learns": a newly approved Mercy-shaped mapping is now the better precedent.
        store.record(_adapter_exemplar("mercy", MERCY_COLUMNS, "mercy-regional"))
        after = retriever.retrieve(query, kind="adapter_mapping", k=1)
        self.assertEqual(after.exemplars[0].payload["adapter_id"], "mercy-regional")

    def test_reviewer_outcomes_become_retrievable_precedent(self):
        store = KnowledgeStore()
        finding = {
            "finding_id": "finding-1", "rule_id": "WC-PI-OMITTED-001", "disposition": "coding_review",
            "proposed_change": {"add_diagnoses": ["L89.154"]}, "subject_ids": ["wound:1"],
        }
        learn_from_decision(store, finding, action="dismissed", reason="documentation_not_supported")
        result = DeterministicRetriever(store).retrieve(
            ["rule:WC-PI-OMITTED-001", "code:L89.154"], kind="review_outcome", k=1,
        )
        self.assertEqual(result.exemplars[0].label, "dismissed:documentation_not_supported")


class PromotionGateTests(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore()

    def _admit(self, score, status="approved-for-demo"):
        return admit_artifact(
            self.store, artifact_id="t1", kind="transform", features=["stage", "pi_stage"],
            payload={"op": "map"}, score=score, status=status,
        )

    def test_passing_artifact_is_promoted_and_recorded(self):
        promoted, exemplar, reason = self._admit(ArtifactScore(parse_rate=1.0, conformance=1.0))
        self.assertTrue(promoted)
        self.assertEqual(reason, "promoted")
        self.assertEqual(len(self.store), 1)
        self.assertEqual(exemplar.label, "approved")

    def test_low_score_is_rejected(self):
        promoted, exemplar, reason = self._admit(ArtifactScore(parse_rate=0.5, conformance=1.0))
        self.assertFalse(promoted)
        self.assertIsNone(exemplar)
        self.assertEqual(len(self.store), 0)

    def test_non_executable_status_is_rejected(self):
        promoted, _, reason = self._admit(ArtifactScore(parse_rate=1.0, conformance=1.0), status="draft")
        self.assertFalse(promoted)
        self.assertIn("not executable", reason)


class TenantScopingTests(unittest.TestCase):
    def _default_chain(self) -> KnowledgeStore:
        store = KnowledgeStore()
        store.record(_adapter_exemplar("a", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))
        store.record(_adapter_exemplar("b", MERCY_COLUMNS, "mercy-regional"))
        return store

    def test_default_tenant_behavior_is_byte_identical(self):
        # Passing tenant_id=None (or the explicit default) must reproduce the pre-tenant chain
        # byte-for-byte: same records, same hashes, same serialization.
        implicit = self._default_chain()
        explicit = KnowledgeStore()
        explicit.record(_adapter_exemplar("a", CLINIC_ALPHA_COLUMNS, "clinic-alpha"), tenant_id=DEFAULT_TENANT_ID)
        explicit.record(_adapter_exemplar("b", MERCY_COLUMNS, "mercy-regional"), tenant_id=DEFAULT_TENANT_ID)
        self.assertEqual(implicit.to_dict(), explicit.to_dict())
        self.assertEqual(implicit.digest, explicit.digest)
        # No `tenants` key leaks in when only the default tenant is used.
        self.assertNotIn("tenants", implicit.to_dict())
        self.assertEqual(len(implicit), 2)
        self.assertTrue(implicit.verify_chain())

    def test_two_tenants_are_isolated_on_retrieval(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("clinic", CLINIC_ALPHA_COLUMNS, "clinic-alpha"), tenant_id="tenant-a")
        store.record(_adapter_exemplar("mercy", MERCY_COLUMNS, "mercy-regional"), tenant_id="tenant-b")
        retriever = DeterministicRetriever(store)
        mercy_query = ["mrn", "encounter_id", "admit_dttm", "icd10_cm", "pi_stage", "poa_flag"]

        # tenant-a holds only the clinic mapping; querying it never returns tenant-b's mercy row.
        a_result = retriever.retrieve(mercy_query, kind="adapter_mapping", k=5, tenant_id="tenant-a")
        self.assertEqual([e.payload["adapter_id"] for e in a_result.exemplars], ["clinic-alpha"])
        b_result = retriever.retrieve(mercy_query, kind="adapter_mapping", k=5, tenant_id="tenant-b")
        self.assertEqual([e.payload["adapter_id"] for e in b_result.exemplars], ["mercy-regional"])

    def test_cross_tenant_leakage_is_impossible(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("clinic", CLINIC_ALPHA_COLUMNS, "clinic-alpha"), tenant_id="tenant-a")
        store.record(_adapter_exemplar("mercy", MERCY_COLUMNS, "mercy-regional"), tenant_id="tenant-b")
        # A tenant only ever sees its own exemplars; the default tenant sees neither.
        self.assertEqual([e.exemplar_id for e in store.exemplars(tenant_id="tenant-a")], ["clinic"])
        self.assertEqual([e.exemplar_id for e in store.exemplars(tenant_id="tenant-b")], ["mercy"])
        self.assertEqual(store.exemplars(tenant_id=DEFAULT_TENANT_ID), [])
        self.assertEqual(store.exemplars(), [])  # implicit default
        self.assertEqual(store.tenants(), ["tenant-a", "tenant-b"])

    def test_each_tenant_chain_verifies_independently(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("clinic", CLINIC_ALPHA_COLUMNS, "clinic-alpha"), tenant_id="tenant-a")
        store.record(_adapter_exemplar("mercy", MERCY_COLUMNS, "mercy-regional"), tenant_id="tenant-b")
        self.assertTrue(store.verify_chain(tenant_id="tenant-a"))
        self.assertTrue(store.verify_chain(tenant_id="tenant-b"))
        self.assertTrue(store.verify_chain())  # all tenants

        # Tamper tenant-a only: its chain breaks, tenant-b's stays intact, whole-store check fails.
        store._records_by_tenant["tenant-a"][0]["content_hash"] = "0" * 64
        self.assertFalse(store.verify_chain(tenant_id="tenant-a"))
        self.assertTrue(store.verify_chain(tenant_id="tenant-b"))
        self.assertFalse(store.verify_chain())

    def test_idempotency_is_scoped_per_tenant(self):
        # Identical content in two different tenants is NOT deduplicated across tenants —
        # each tenant's chain is independent — but is idempotent within a tenant.
        store = KnowledgeStore()
        store.record(_adapter_exemplar("a", CLINIC_ALPHA_COLUMNS, "clinic-alpha"), tenant_id="tenant-a")
        store.record(_adapter_exemplar("a2", CLINIC_ALPHA_COLUMNS, "clinic-alpha"), tenant_id="tenant-a")
        store.record(_adapter_exemplar("b", CLINIC_ALPHA_COLUMNS, "clinic-alpha"), tenant_id="tenant-b")
        self.assertEqual(len(store.exemplars(tenant_id="tenant-a")), 1)
        self.assertEqual(len(store.exemplars(tenant_id="tenant-b")), 1)

    def test_roundtrip_preserves_all_tenants(self):
        store = KnowledgeStore()
        store.record(_adapter_exemplar("d", CLINIC_ALPHA_COLUMNS, "clinic-alpha"))  # default
        store.record(_adapter_exemplar("mercy", MERCY_COLUMNS, "mercy-regional"), tenant_id="tenant-b")
        restored = KnowledgeStore.from_dict(store.to_dict())
        self.assertEqual(restored.to_dict(), store.to_dict())
        self.assertEqual(restored.tenant_digest("tenant-b"), store.tenant_digest("tenant-b"))
        self.assertTrue(restored.verify_chain())
        self.assertEqual([e.exemplar_id for e in restored.exemplars(tenant_id="tenant-b")], ["mercy"])

    def test_admit_and_learn_carry_tenant_through(self):
        store = KnowledgeStore()
        promoted, exemplar, _ = admit_artifact(
            store, artifact_id="t1", kind="transform", features=["stage", "pi_stage"],
            payload={"op": "map"}, score=ArtifactScore(parse_rate=1.0, conformance=1.0),
            status="approved-for-demo", tenant_id="tenant-a",
        )
        self.assertTrue(promoted)
        finding = {
            "finding_id": "finding-1", "rule_id": "WC-PI-OMITTED-001", "disposition": "coding_review",
            "proposed_change": {"add_diagnoses": ["L89.154"]}, "subject_ids": ["wound:1"],
        }
        learn_from_decision(store, finding, action="dismissed", reason="x", tenant_id="tenant-a")
        # Both writes landed in tenant-a; default tenant is empty.
        self.assertEqual(len(store.exemplars(tenant_id="tenant-a")), 2)
        self.assertEqual(store.exemplars(), [])
        self.assertTrue(store.verify_chain(tenant_id="tenant-a"))


if __name__ == "__main__":
    unittest.main()
