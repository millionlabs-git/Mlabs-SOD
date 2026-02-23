import dotenv from 'dotenv';
dotenv.config();

export const config = {
  port: parseInt(process.env.PORT || '8080', 10),
  databaseUrl: process.env.DATABASE_URL!,
  webhookSecret: process.env.WEBHOOK_SECRET!,
  gcpProjectId: process.env.GCP_PROJECT_ID!,
  gcpRegion: process.env.GCP_REGION || 'europe-west1',
  gcpServiceAccountKey: process.env.GCP_SERVICE_ACCOUNT_KEY || '',
  workerJobName: process.env.WORKER_JOB_NAME || 'prd-worker',
  pollIntervalMs: parseInt(process.env.POLL_INTERVAL_MS || '5000', 10),
  orchestratorUrl: process.env.ORCHESTRATOR_URL!,
  dryRun: process.env.DRY_RUN === 'true',
} as const;

const required: (keyof typeof config)[] = [
  'databaseUrl',
  'webhookSecret',
  'orchestratorUrl',
];

// Only require GCP vars when not in dry run mode
const gcpRequired: (keyof typeof config)[] = [
  'gcpProjectId',
  'gcpServiceAccountKey',
];

export function validateConfig(): void {
  const missing = required.filter((key) => !config[key]);
  if (!config.dryRun) {
    missing.push(...gcpRequired.filter((key) => !config[key]));
  }
  if (missing.length > 0) {
    throw new Error(`Missing required env vars: ${missing.join(', ')}`);
  }
}
