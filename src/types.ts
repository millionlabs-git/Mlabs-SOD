export type JobMode = 'full-build' | 'deploy-only' | 'auto';

export interface Job {
  id: string;
  repo_url: string;
  branch: string;
  prd_path: string;
  mode: JobMode;
  status: JobStatus;
  metadata: Record<string, unknown> | null;
  callback_url: string | null;
  cloud_run_execution_id: string | null;
  pr_url: string | null;
  live_url: string | null;
  fly_app_name: string | null;
  neon_project_id: string | null;
  build_status: string;
  build_message: string;
  created_at: Date;
  updated_at: Date;
}

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed';

export type BuildStatus =
  | 'queued'
  | 'cloning'
  | 'installing'
  | 'building'
  | 'testing'
  | 'deploying'
  | 'deployed'
  | 'completed'
  | 'error'
  | 'failed'
  | 'cancelled';

export interface JobEvent {
  id: string;
  job_id: string;
  event: string;
  detail: Record<string, unknown> | null;
  created_at: Date;
}

export interface JobWithEvents extends Job {
  events: JobEvent[];
}
