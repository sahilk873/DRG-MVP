# Encounter pitch demo

An interactive, synthetic product walkthrough for the Encounter revenue-integrity platform. It demonstrates the product boundary from variable provider data through human-authorized review without suggesting that a model makes coding or payment decisions.

## Run locally

```bash
cd demo
npm ci
npm run dev
```

Open the URL printed by Vite. Use **Start guided demo** for the narrated five-step pitch flow.

## Suggested three-minute pitch

1. **Command center:** run the synthetic scan and establish the outcome—evidence-complete opportunities, not another worklist of weak guesses.
2. **Data onboarding:** show how a bounded profile helps an agent draft a reusable provider adapter while deterministic software processes the bulk dataset.
3. **Encounter graph:** open the stage 4 pressure-injury case and move between exact evidence, patient ontology, claim comparison, and audit history.
4. **Financial simulation:** show submitted DRG 871 versus the illustrative candidate 870, emphasizing that licensed grouping and coder confirmation remain required.
5. **Review queue:** route the case to coding and close on the operating model: automation prepares the packet; people decide only the consequential exceptions.

## Demo boundaries

- Every patient, facility, claim, metric, and payment amount is synthetic or illustrative.
- The interface never mutates or submits a claim.
- Model output is limited to bounded mapping proposals and evidence extraction.
- Ontology validation, rule evaluation, grouping, pricing, and audit behavior are represented as deterministic controls.

## Verification

```bash
npm run typecheck
npm run build
```
