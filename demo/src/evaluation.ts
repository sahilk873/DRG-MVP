import { z } from 'zod'

const nonEmpty = z.string().min(1)

export const evaluationReportSchema = z.object({
  eval_schema_version: z.literal('1.0.0'),
  basis: nonEmpty,
  engine_version: nonEmpty,
  case_count: z.number().int().nonnegative(),
  label_count: z.number().int().nonnegative(),
  metrics: z.object({
    true_positives: z.number().int().nonnegative(),
    false_positives: z.number().int().nonnegative(),
    false_negatives: z.number().int().nonnegative(),
    precision: z.number().min(0).max(1),
    recall: z.number().min(0).max(1),
    f1: z.number().min(0).max(1),
  }).strict(),
  thresholds: z.object({
    min_precision: z.number().min(0).max(1).optional(),
    min_recall: z.number().min(0).max(1).optional(),
    min_f1: z.number().min(0).max(1).optional(),
  }).partial().optional(),
  passed: z.boolean().optional(),
  report_hash: z.string().regex(/^[0-9a-f]{64}$/),
}).strict()

export type EvaluationReport = z.infer<typeof evaluationReportSchema>

export function parseEvaluationReport(value: unknown): EvaluationReport {
  return evaluationReportSchema.parse(value)
}
