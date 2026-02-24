import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { config } from '../config';
import { createJob, findExistingJob } from '../db/queries';
import { sendBuildEvent } from '../webhook/notifier';

const webhookBody = z.object({
  repo_url: z.string().url().refine(
    (url: string) => url.includes('github.com'),
    { message: 'Must be a valid GitHub URL' }
  ),
  branch: z.string().default('main'),
  prd_path: z.string().default('docs/PRD.md'),
  mode: z.enum(['full-build', 'deploy-only', 'auto']).default('full-build'),
  metadata: z.record(z.string(), z.unknown()).optional(),
  callback_url: z.string().url().optional(),
});

export const webhookRouter = Router();

webhookRouter.post('/webhook', async (req: Request, res: Response) => {
  const authHeader = req.headers.authorization;
  if (!authHeader || authHeader !== `Bearer ${config.webhookSecret}`) {
    res.status(401).json({ error: 'Unauthorized' });
    return;
  }

  const parsed = webhookBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'Validation failed', details: parsed.error.issues });
    return;
  }

  const data = parsed.data;

  // Dedup: if a pending/running job exists for same repo+branch, return it
  const existing = await findExistingJob(data.repo_url, data.branch);
  if (existing) {
    res.status(200).json({ job_id: existing.id, status: existing.status, deduplicated: true });
    return;
  }

  const job = await createJob({
    repo_url: data.repo_url,
    branch: data.branch,
    prd_path: data.prd_path,
    mode: data.mode,
    metadata: data.metadata,
    callback_url: data.callback_url,
  });

  // Notify MillionScopes that the job has been queued (fire-and-forget)
  sendBuildEvent({
    job_id: job.id,
    status: 'queued',
    message: 'Build queued',
  }).catch(() => {});

  res.status(201).json({ job_id: job.id, status: 'pending' });
});
