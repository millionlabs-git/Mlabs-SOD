import { config } from '../config';
import { Job } from '../types';

interface LaunchResult {
  executionId: string;
}

export async function launchWorker(job: Job): Promise<LaunchResult> {
  if (config.dryRun) {
    const fakeId = `dry-run-${job.id.slice(0, 8)}`;
    console.log(`[DRY RUN] Would launch worker for job ${job.id}`, {
      repo_url: job.repo_url,
      branch: job.branch,
      prd_path: job.prd_path,
    });
    return { executionId: fakeId };
  }

  const { JobsClient } = await import('@google-cloud/run');

  const credentials = JSON.parse(
    Buffer.from(config.gcpServiceAccountKey, 'base64').toString()
  );

  const client = new JobsClient({ credentials });

  const jobName = `projects/${config.gcpProjectId}/locations/${config.gcpRegion}/jobs/${config.workerJobName}`;

  const [operation] = await client.runJob({
    name: jobName,
    overrides: {
      containerOverrides: [
        {
          env: [
            { name: 'JOB_ID', value: job.id },
            { name: 'REPO_URL', value: job.repo_url },
            { name: 'BRANCH', value: job.branch },
            { name: 'PRD_PATH', value: job.prd_path },
            { name: 'ORCHESTRATOR_URL', value: config.orchestratorUrl },
            { name: 'WEBHOOK_SECRET', value: config.webhookSecret },
          ],
        },
      ],
    },
  });

  // Don't await operation.promise() — it blocks until the Cloud Run job
  // finishes, which can take 30-60+ minutes and causes gRPC timeouts.
  // The worker reports progress via event callbacks; the orchestrator
  // detects completion/failure from terminal events + stale job recovery.
  const executionId = operation.metadata?.name || `unknown-${Date.now()}`;

  console.log(`Launched worker for job ${job.id}, execution: ${executionId}`);
  return { executionId };
}
