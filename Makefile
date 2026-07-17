.PHONY: test typecheck demo-ui-check verify demo bulk-profile bulk-demo

test:
	PYTHONPATH=src python -m unittest discover -s tests -v

typecheck:
	cd agent && npm run check

demo-ui-check:
	cd demo && npm run typecheck && npm run build

verify: test typecheck demo-ui-check

demo:
	PYTHONPATH=src python -m revenue_integrity.cli examples/case_pressure_injury.json rules/wound_care_v1.json

bulk-profile:
	PYTHONPATH=src python -m revenue_integrity.ingestion.cli profile examples/bulk/clinic_alpha --output /tmp/clinic-alpha.profile.json

bulk-demo:
	PYTHONPATH=src python -m revenue_integrity.ingestion.cli run examples/bulk/clinic_alpha examples/adapters/clinic_alpha_wound_care_v1.json --output-directory /tmp/clinic-alpha-source-bundles --report /tmp/clinic-alpha.run.json
