import { Mastra } from '@mastra/core/mastra'

import { encounterExtractionAgent } from '../agents/encounter-extractor.ts'
import { adapterDesignerAgent } from '../agents/adapter-designer.ts'

export const mastra = new Mastra({
  agents: {
    adapterDesignerAgent,
    encounterExtractionAgent,
  },
})
