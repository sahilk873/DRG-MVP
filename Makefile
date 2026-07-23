.PHONY: test typecheck demo-packet demo-packet-check demo-ui-check verify demo eval bulk-profile bulk-demo bulk-demo-mercy

test:
	PYTHONPATH=src python -m unittest discover -s tests -v

eval:
	PYTHONPATH=src python -m revenue_integrity.eval_cli examples/evaluation/gold_manifest.json --enforce

typecheck:
	cd agent && npm run check

demo-packet:
	PYTHONPATH=src python scripts/generate_demo_packet.py

demo-packet-check:
	PYTHONPATH=src python scripts/generate_demo_packet.py --check

demo-ui-check: demo-packet-check
	cd demo && npm run test && npm run typecheck && npm run build

verify: test typecheck demo-ui-check

demo:
	PYTHONPATH=src python -m revenue_integrity.cli examples/case_pressure_injury.json rules/wound_care_v1.json

bulk-profile:
	PYTHONPATH=src python -m revenue_integrity.ingestion.cli profile examples/bulk/clinic_alpha --output /tmp/clinic-alpha.profile.json

bulk-demo:
	PYTHONPATH=src python -m revenue_integrity.ingestion.cli run examples/bulk/clinic_alpha examples/adapters/clinic_alpha_wound_care_v1.json --output-directory /tmp/clinic-alpha-source-bundles --report /tmp/clinic-alpha.run.json

bulk-demo-mercy:
	PYTHONPATH=src python -m revenue_integrity.ingestion.cli profile examples/bulk/mercy_regional --output /tmp/mercy.profile.json
	PYTHONPATH=src python -m revenue_integrity.ingestion.cli run examples/bulk/mercy_regional examples/adapters/mercy_regional_wound_care_v1.json --output-directory /tmp/mercy-source-bundles --report /tmp/mercy.run.json
