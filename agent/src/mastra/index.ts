import { Mastra } from '@mastra/core/mastra'

import { encounterExtractionAgent } from '../agents/encounter-extractor.ts'
import { adapterDesignerAgent } from '../agents/adapter-designer.ts'
import { createClinicalFinancialReconciler } from '../agents/billing-reconciler.ts'
import { createInvestigationCritic } from '../agents/investigation-critic.ts'

export const mastra = new Mastra({
  agents: {
    adapterDesignerAgent,
    encounterExtractionAgent,
    clinicalFinancialReconciler: createClinicalFinancialReconciler(),
    investigationCritic: createInvestigationCritic(),
  },
})
