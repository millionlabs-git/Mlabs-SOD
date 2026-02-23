import express from 'express';
import fs from 'fs';
import path from 'path';
import { config, validateConfig } from './config';
import { pool, checkConnection } from './db/client';
import { webhookRouter } from './routes/webhook';
import { statusRouter } from './routes/status';
import { startProcessor, stopProcessor } from './queue/processor';

async function bootstrap(): Promise<void> {
  // Run schema.sql to ensure tables exist
  const schemaPath = path.join(__dirname, 'db', 'schema.sql');
  const schema = fs.readFileSync(schemaPath, 'utf-8');
  await pool.query(schema);
  console.log('Database schema applied');
}

async function main(): Promise<void> {
  validateConfig();

  await checkConnection();
  console.log('Database connected');

  await bootstrap();

  const app = express();
  app.use(express.json({ limit: '10kb' }));

  // Health check
  app.get('/health', async (_req, res) => {
    try {
      await checkConnection();
      res.json({ status: 'ok' });
    } catch {
      res.status(503).json({ status: 'unhealthy' });
    }
  });

  app.use(webhookRouter);
  app.use(statusRouter);

  startProcessor();

  const server = app.listen(config.port, () => {
    console.log(`Orchestrator listening on port ${config.port}`);
    if (config.dryRun) {
      console.log('DRY_RUN mode enabled — workers will not be launched');
    }
  });

  // Graceful shutdown
  const shutdown = async () => {
    console.log('Shutting down...');
    stopProcessor();
    server.close();
    await pool.end();
    process.exit(0);
  };

  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});
