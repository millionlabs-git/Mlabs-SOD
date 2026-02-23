import { config, validateConfig } from './config';
import { pool } from './db/client';
import { createJob, addJobEvent } from './db/queries';

async function seed() {
  validateConfig();

  const job = await createJob({
    repo_url: 'https://github.com/example/test-repo',
    branch: 'main',
    prd_path: 'docs/PRD.md',
    metadata: { project_id: 'test-123', customer: 'seed-script' },
    callback_url: null,
  });

  console.log(`Created test job: ${job.id}`);

  // Add a sample event
  await addJobEvent(job.id, 'seed_created', { note: 'Created by seed script' });
  console.log('Added seed event');

  await pool.end();
}

seed().catch((err) => {
  console.error('Seed error:', err);
  process.exit(1);
});
