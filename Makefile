.PHONY: test typecheck verify demo

test:
	PYTHONPATH=src python -m unittest discover -s tests -v

typecheck:
	cd agent && npm run check

verify: test typecheck

demo:
	PYTHONPATH=src python -m revenue_integrity.cli examples/case_pressure_injury.json rules/wound_care_v1.json
