export interface Job {
  id: string;
  repo_url: string;
  branch: string;
  prd_path: string;
  status: JobStatus;
  metadata: Record<string, unknown> | null;
  callback_url: string | null;
  cloud_run_execution_id: string | null;
  pr_url: string | null;
  live_url: string | null;
  netlify_site_id: string | null;
  neon_project_id: string | null;
  created_at: Date;
  updated_at: Date;
}

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed';

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
