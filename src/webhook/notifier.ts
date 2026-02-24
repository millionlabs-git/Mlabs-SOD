import { config } from '../config';
import { BuildStatus } from '../types';
import { updateBuildStatus } from '../db/queries';

interface BuildEventPayload {
  job_id: string;
  status: BuildStatus;
  message: string;
  metadata?: Record<string, unknown>;
}

/**
 * Maps internal worker event names to MillionScopes-compatible build statuses.
 */
const EVENT_STATUS_MAP: Record<string, { status: BuildStatus; message: string }> = {
  worker_launched: { status: 'queued', message: 'Worker launched' },
  worker_started: { status: 'queued', message: 'Build starting...' },
  repo_cloned: { status: 'cloning', message: 'Repository cloned' },
  prd_parsed: { status: 'building', message: 'PRD parsed, planning build...' },
  orchestrator_started: { status: 'building', message: 'Building application...' },
  orchestrator_complete: { status: 'building', message: 'Build complete, preparing for deployment...' },
  deploy_started: { status: 'deploying', message: 'Starting deployment...' },
  neon_provisioning: { status: 'deploying', message: 'Provisioning database...' },
  schema_migrating: { status: 'deploying', message: 'Running database migrations...' },
  readiness_check: { status: 'deploying', message: 'Checking deployment readiness...' },
  readiness_passed: { status: 'deploying', message: 'Deployment readiness check passed' },
  readiness_fixing: { status: 'deploying', message: 'Fixing build issues before deployment...' },
  readiness_failed: { status: 'error', message: 'Deployment readiness check failed' },
  flyio_deploying: { status: 'deploying', message: 'Deploying to Fly.io...' },
  deploy_verifying: { status: 'deploying', message: 'Verifying deployment...' },
  deployed: { status: 'deployed', message: 'Deployed successfully' },
  completed: { status: 'deployed', message: 'Build completed successfully' },
  build_complete: { status: 'deployed', message: 'Build completed successfully' },
  build_failed: { status: 'failed', message: 'Build failed' },
  failed: { status: 'failed', message: 'Build failed' },
  launch_failed: { status: 'error', message: 'Failed to launch build worker' },
  pr_created: { status: 'building', message: 'Pull request created' },
};

/**
 * Send a build progress event to MillionScopes.
 * Fire-and-forget — logs errors but never throws.
 */
export async function sendBuildEvent(payload: BuildEventPayload): Promise<void> {
  if (!config.millionscopesWebhookUrl || !config.webhookBearerToken) {
    return;
  }

  const url = `${config.millionscopesWebhookUrl}/api/webhook/build-event`;

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${config.webhookBearerToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      console.error(
        `[webhook] Failed to send build event to MillionScopes: ${resp.status} ${resp.statusText}`
      );
    }
  } catch (err) {
    console.error('[webhook] Error sending build event to MillionScopes:', err);
  }
}

/**
 * Maps an internal worker event to a MillionScopes build event and sends it.
 * Also updates the job's build_status and build_message in the database.
 */
export async function forwardEventToMillionScopes(
  jobId: string,
  event: string,
  detail?: Record<string, unknown> | null
): Promise<void> {
  const mapping = EVENT_STATUS_MAP[event];
  if (!mapping) {
    // Unknown event — skip forwarding
    return;
  }

  const status = mapping.status;
  // Use detail message if available, otherwise use the default mapping message
  const message = (detail?.message as string) || mapping.message;
  const metadata: Record<string, unknown> = { event, ...detail };

  // Update the job's build_status/build_message for the polling endpoint
  try {
    await updateBuildStatus(jobId, status, message);
  } catch (err) {
    console.error(`[webhook] Failed to update build status for job ${jobId}:`, err);
  }

  await sendBuildEvent({
    job_id: jobId,
    status,
    message,
    metadata,
  });
}
