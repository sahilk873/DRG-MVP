import { mkdir, open, readFile, rename, rm } from 'node:fs/promises'
import { dirname, basename, join } from 'node:path'

import { extractEncounterCase } from './agents/encounter-extractor.ts'

async function main(): Promise<void> {
  const [, , inputPath, outputPath] = process.argv
  if (!inputPath || !outputPath) {
    throw new Error('Usage: npm run extract -- <source-bundle.json> <encounter-case.json>')
  }

  const sourceBundle = JSON.parse(await readFile(inputPath, 'utf8'))
  const encounterCase = await extractEncounterCase(sourceBundle)
  await atomicWrite(outputPath, `${JSON.stringify(encounterCase, null, 2)}\n`)
  console.log(`Validated encounter case written to ${outputPath}`)
}

async function atomicWrite(outputPath: string, content: string): Promise<void> {
  await mkdir(dirname(outputPath), { recursive: true })
  const temporaryPath = join(dirname(outputPath), `.${basename(outputPath)}.${process.pid}.tmp`)
  try {
    const file = await open(temporaryPath, 'wx')
    try {
      await file.writeFile(content, 'utf8')
      await file.sync()
    } finally {
      await file.close()
    }
    await rename(temporaryPath, outputPath)
  } catch (error) {
    await rm(temporaryPath, { force: true })
    throw error
  }
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(`error: ${message}`)
  process.exitCode = 1
})
