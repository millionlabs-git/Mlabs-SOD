import { pool } from './client';
import { Job, JobEvent, JobStatus, BuildStatus } from '../types';

interface CreateJobParams {
  repo_url: string;
  branch: string;
  prd_path: string;
  mode?: string;
  metadata?: Record<string, unknown> | null;
  callback_url?: string | null;
}

export async function createJob(params: CreateJobParams): Promise<Job> {
  const { rows } = await pool.query<Job>(
    `INSERT INTO jobs (repo_url, branch, prd_path, mode, metadata, callback_url)
     VALUES ($1, $2, $3, $4, $5, $6)
     RETURNING *`,
    [
      params.repo_url,
      params.branch,
      params.prd_path,
      params.mode || 'full-build',
      params.metadata ? JSON.stringify(params.metadata) : null,
      params.callback_url || null,
    ]
  );
  return rows[0];
}

export async function findExistingJob(
  repoUrl: string,
  branch: string
): Promise<Job | null> {
  const { rows } = await pool.query<Job>(
    `SELECT * FROM jobs
     WHERE repo_url = $1 AND branch = $2 AND status IN ('pending', 'running')
     ORDER BY created_at DESC LIMIT 1`,
    [repoUrl, branch]
  );
  return rows[0] || null;
}

export async function getJob(id: string): Promise<Job | null> {
  const { rows } = await pool.query<Job>(
    'SELECT * FROM jobs WHERE id = $1',
    [id]
  );
  return rows[0] || null;
}

export async function getNextPendingJob(): Promise<Job | null> {
  const { rows } = await pool.query<Job>(
    `SELECT * FROM jobs
     WHERE status = 'pending'
     ORDER BY created_at
     LIMIT 1
     FOR UPDATE SKIP LOCKED`
  );
  return rows[0] || null;
}

export async function countRunningJobs(): Promise<number> {
  const { rows } = await pool.query<{ count: string }>(
    `SELECT COUNT(*) as count FROM jobs WHERE status = 'running'`
  );
  return parseInt(rows[0].count, 10);
}

export async function updateJobStatus(
  id: string,
  status: JobStatus,
  executionId?: string
): Promise<void> {
  if (executionId) {
    await pool.query(
      `UPDATE jobs SET status = $1, cloud_run_execution_id = $2, updated_at = now() WHERE id = $3`,
      [status, executionId, id]
    );
  } else {
    await pool.query(
      `UPDATE jobs SET status = $1, updated_at = now() WHERE id = $2`,
      [status, id]
    );
  }
}

export async function addJobEvent(
  jobId: string,
  event: string,
  detail?: Record<string, unknown> | null
): Promise<void> {
  await pool.query(
    `INSERT INTO job_events (job_id, event, detail) VALUES ($1, $2, $3)`,
    [jobId, event, detail ? JSON.stringify(detail) : null]
  );
}

export async function getJobEvents(jobId: string): Promise<JobEvent[]> {
  const { rows } = await pool.query<JobEvent>(
    'SELECT * FROM job_events WHERE job_id = $1 ORDER BY created_at',
    [jobId]
  );
  return rows;
}

export async function updateBuildStatus(
  id: string,
  buildStatus: BuildStatus,
  buildMessage: string
): Promise<void> {
  await pool.query(
    `UPDATE jobs SET build_status = $1, build_message = $2, updated_at = now() WHERE id = $3`,
    [buildStatus, buildMessage, id]
  );
}

export async function markStaleJobsFailed(
  timeoutMinutes: number = 30
): Promise<number> {
  const { rowCount } = await pool.query(
    `UPDATE jobs
     SET status = 'failed', updated_at = now()
     WHERE status = 'running'
       AND updated_at < now() - interval '1 minute' * $1`,
    [timeoutMinutes]
  );
  return rowCount ?? 0;
}
