import { config } from '../config';
import {
  getNextPendingJob,
  updateJobStatus,
  addJobEvent,
  countRunningJobs,
  markStaleJobsFailed,
} from '../db/queries';
import { launchWorker } from '../cloud-run/launcher';

const MAX_CONCURRENT_JOBS = 5;

let running = false;
let timer: ReturnType<typeof setInterval> | null = null;

async function processNext(): Promise<void> {
  try {
    // Check concurrency limit
    const runningCount = await countRunningJobs();
    if (runningCount >= MAX_CONCURRENT_JOBS) {
      return;
    }

    const job = await getNextPendingJob();
    if (!job) return;

    console.log(`Processing job ${job.id} (${job.repo_url}@${job.branch})`);

    await updateJobStatus(job.id, 'running');

    try {
      const { executionId } = await launchWorker(job);
      await updateJobStatus(job.id, 'running', executionId);
      await addJobEvent(job.id, 'worker_launched', { execution_id: executionId });
    } catch (err) {
      console.error(`Failed to launch worker for job ${job.id}:`, err);
      await updateJobStatus(job.id, 'failed');
      await addJobEvent(job.id, 'launch_failed', {
        error: err instanceof Error ? err.message : String(err),
      });
    }
  } catch (err) {
    console.error('Processor error:', err);
  }
}

async function recoverStaleJobs(): Promise<void> {
  try {
    const count = await markStaleJobsFailed(30);
    if (count > 0) {
      console.log(`Recovered ${count} stale jobs (marked as failed)`);
    }
  } catch (err) {
    console.error('Stale job recovery error:', err);
  }
}

export function startProcessor(): void {
  if (running) return;
  running = true;

  console.log(`Job processor started (poll interval: ${config.pollIntervalMs}ms)`);

  // Run stale recovery on startup
  recoverStaleJobs();

  timer = setInterval(async () => {
    await processNext();
  }, config.pollIntervalMs);

  // Also run stale recovery every 5 minutes
  setInterval(() => {
    recoverStaleJobs();
  }, 5 * 60 * 1000);
}

export function stopProcessor(): void {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  running = false;
  console.log('Job processor stopped');
}
