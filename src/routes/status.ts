import { Router, Request, Response } from 'express';
import { z } from 'zod';
import { config } from '../config';
import { getJob, getJobEvents, addJobEvent, updateJobStatus } from '../db/queries';

export const statusRouter = Router();

// GET /jobs/:id/status — public job status
statusRouter.get('/jobs/:id/status', async (req: Request, res: Response) => {
  const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
  const job = await getJob(id);
  if (!job) {
    res.status(404).json({ error: 'Job not found' });
    return;
  }

  const events = await getJobEvents(job.id);

  res.json({
    id: job.id,
    status: job.status,
    repo_url: job.repo_url,
    branch: job.branch,
    prd_path: job.prd_path,
    cloud_run_execution_id: job.cloud_run_execution_id,
    created_at: job.created_at,
    updated_at: job.updated_at,
    events: events.map((e) => ({
      event: e.event,
      detail: e.detail,
      created_at: e.created_at,
    })),
  });
});

// POST /jobs/:id/events — worker status callback
const eventBody = z.object({
  event: z.string().min(1),
  detail: z.record(z.string(), z.unknown()).optional(),
});

statusRouter.post('/jobs/:id/events', async (req: Request, res: Response) => {
  const authHeader = req.headers.authorization;
  if (!authHeader || authHeader !== `Bearer ${config.webhookSecret}`) {
    res.status(401).json({ error: 'Unauthorized' });
    return;
  }

  const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
  const job = await getJob(id);
  if (!job) {
    res.status(404).json({ error: 'Job not found' });
    return;
  }

  const parsed = eventBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'Validation failed', details: parsed.error.issues });
    return;
  }

  const { event, detail } = parsed.data;

  await addJobEvent(job.id, event, detail);

  // Terminal events update the job status
  if (event === 'completed' || event === 'failed') {
    await updateJobStatus(job.id, event);
  }

  // Forward to callback URL if configured (fire-and-forget)
  if (job.callback_url) {
    fetch(job.callback_url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: job.id, event, detail }),
    }).catch(() => {
      // Intentionally swallowed — fire-and-forget
    });
  }

  res.status(201).json({ ok: true });
});
