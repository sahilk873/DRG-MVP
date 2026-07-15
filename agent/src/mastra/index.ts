import { Mastra } from '@mastra/core/mastra'

import { encounterExtractionAgent } from '../agents/encounter-extractor.ts'

export const mastra = new Mastra({
  agents: {
    encounterExtractionAgent,
  },
})

