import type { Agent } from '@mastra/core/agent'

import { z } from 'zod'

import {
  documentationObservationSchema,
  investigationPacketSchema,
  opportunityCritiqueSchema,
  opportunityHypothesisSchema,
  type DocumentationObservation,
  type InvestigationPacket,
  type OpportunityCritique,
  type OpportunityHypothesis,
} from '../schema.ts'
import { critiqueDocumentation } from '../agents/documentation-critic.ts'
import { critiqueInvestigationOpportunities, synthesizeOpportunities } from '../agents/investigation-critic.ts'
import { reconcileClinicalFinancialOpportunities } from '../agents/billing-reconciler.ts'
import { resolveModelId } from '../agents/encounter-extractor.ts'

export const INVESTIGATION_WORKFLOW_ID = 'clinical-financial-investigation-workflow'
export const INVESTIGATION_WORKFLOW_VERSION = '1.0.0'

/**
 * Fixed, deterministic step order. The workflow orchestrates existing agents; it
 * never emits authoritative coding, DRG, payment, or claim fields. The single
 * output bundle carries only evidence-grounded hypotheses, adversarial critiques,
 * accepted (deduped) hypotheses, and advisory documentation observations for the
 * deterministic Python orchestrator to validate.
 */
export const INVESTIGATION_WORKFLOW_STEPS = [
  'reconcile',
  'critique-opportunities',
  'synthesize',
  'critique-documentation',
] as const

export const investigationBundleSchema = z.object({
  workflow_id: z.literal(INVESTIGATION_WORKFLOW_ID),
  workflow_version: z.literal(INVESTIGATION_WORKFLOW_VERSION),
  packet_id: z.string().trim().min(1),
  encounter_id: z.string().trim().min(1),
  model_id: z.string().trim().min(1),
  steps: z.array(z.enum(INVESTIGATION_WORKFLOW_STEPS)),
  hypotheses: z.array(opportunityHypothesisSchema),
  critiques: z.array(opportunityCritiqueSchema),
  accepted_hypotheses: z.array(opportunityHypothesisSchema),
  documentation_observations: z.array(documentationObservationSchema),
}).strict()

export type InvestigationBundle = z.infer<typeof investigationBundleSchema>

export interface InvestigationWorkflowAgents {
  reconciler?: Agent
  opportunityCritic?: Agent
  documentationCritic?: Agent
}

export interface InvestigationWorkflowOptions {
  modelId?: string
  agents?: InvestigationWorkflowAgents
}

/**
 * Run the investigation workflow: reconcile -> critique opportunities ->
 * synthesize -> critique documentation, in that fixed order. Each step consumes
 * only the outputs of prior steps plus the scoped packet. The result is a single
 * schema-constrained bundle; the Python orchestrator remains authoritative.
 */
export async function runInvestigationWorkflow(
  rawPacket: unknown,
  options: InvestigationWorkflowOptions = {},
): Promise<InvestigationBundle> {
  const packet: InvestigationPacket = investigationPacketSchema.parse(rawPacket)
  const modelId = options.modelId ?? resolveModelId()
  const agents = options.agents ?? {}

  // Step 1: evidence-grounded hypotheses (missed revenue + unsupported-billing risk).
  const { hypotheses } = await reconcileClinicalFinancialOpportunities(packet, {
    agent: agents.reconciler,
    modelId,
  })

  // Step 2: adversarial critique of every hypothesis.
  const critiques = await critiqueOpportunityStep(packet, hypotheses, agents.opportunityCritic, modelId)

  // Step 3: deterministic synthesis — keep supported, rank, dedupe.
  const acceptedHypotheses: OpportunityHypothesis[] = synthesizeOpportunities(hypotheses, critiques)

  // Step 4: advisory documentation-gap observations over the reconstructed record.
  const { observations } = await critiqueDocumentation(packet.encounter, {
    agent: agents.documentationCritic,
    modelId,
  })

  return investigationBundleSchema.parse({
    workflow_id: INVESTIGATION_WORKFLOW_ID,
    workflow_version: INVESTIGATION_WORKFLOW_VERSION,
    packet_id: packet.packet_id,
    encounter_id: packet.encounter.encounter_id,
    model_id: modelId,
    steps: [...INVESTIGATION_WORKFLOW_STEPS],
    hypotheses,
    critiques,
    accepted_hypotheses: acceptedHypotheses,
    documentation_observations: observations,
  } satisfies InvestigationBundle)
}

async function critiqueOpportunityStep(
  packet: InvestigationPacket,
  hypotheses: readonly OpportunityHypothesis[],
  agent: Agent | undefined,
  modelId: string,
): Promise<OpportunityCritique[]> {
  if (hypotheses.length === 0) return []
  const { critiques } = await critiqueInvestigationOpportunities(packet, hypotheses, { agent, modelId })
  return critiques
}

export type { DocumentationObservation, OpportunityCritique, OpportunityHypothesis }
