import { Router, Request, Response } from 'express';
import { config } from '../config';
import { getJob, getJobEvents } from '../db/queries';

export const dashboardRouter = Router();

// ---------------------------------------------------------------------------
// SSE endpoint: GET /jobs/:id/stream
// ---------------------------------------------------------------------------
dashboardRouter.get('/jobs/:id/stream', async (req: Request, res: Response) => {
  const token = req.query.token as string | undefined;
  const authHeader = req.headers.authorization;
  const bearerToken = token || (authHeader ? authHeader.replace('Bearer ', '') : '');

  if (bearerToken !== config.webhookSecret) {
    res.status(401).json({ error: 'Unauthorized' });
    return;
  }

  const id = Array.isArray(req.params.id) ? req.params.id[0] : req.params.id;
  const job = await getJob(id);
  if (!job) {
    res.status(404).json({ error: 'Job not found' });
    return;
  }

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });

  const send = (data: unknown) => {
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  // Send all existing events
  const existing = await getJobEvents(id);
  for (const evt of existing) {
    send({
      event: evt.event,
      detail: evt.detail,
      created_at: evt.created_at,
      job_status: job.status,
      build_status: job.build_status,
    });
  }

  let lastSeen = existing.length > 0 ? existing[existing.length - 1].created_at : new Date(0);
  let closed = false;

  req.on('close', () => {
    closed = true;
  });

  // Keep-alive ping every 15s
  const keepAlive = setInterval(() => {
    if (closed) return;
    res.write(':ping\n\n');
  }, 15_000);

  // Poll for new events every 2s
  const poll = setInterval(async () => {
    if (closed) {
      clearInterval(poll);
      clearInterval(keepAlive);
      return;
    }

    try {
      const freshJob = await getJob(id);
      const events = await getJobEvents(id);
      const newEvents = events.filter(
        (e) => new Date(e.created_at) > new Date(lastSeen as unknown as string)
      );

      for (const evt of newEvents) {
        send({
          event: evt.event,
          detail: evt.detail,
          created_at: evt.created_at,
          job_status: freshJob?.status,
          build_status: freshJob?.build_status,
        });
      }

      if (newEvents.length > 0) {
        lastSeen = newEvents[newEvents.length - 1].created_at;
      }

      // Close when terminal
      if (freshJob && (freshJob.status === 'completed' || freshJob.status === 'failed')) {
        send({ event: 'stream_end', detail: { final_status: freshJob.status }, created_at: new Date(), job_status: freshJob.status, build_status: freshJob.build_status });
        clearInterval(poll);
        clearInterval(keepAlive);
        res.end();
      }
    } catch {
      // Swallow polling errors — stream will retry or client will reconnect
    }
  }, 2_000);
});

// ---------------------------------------------------------------------------
// HTML dashboard: GET /dashboard/:id
// ---------------------------------------------------------------------------
dashboardRouter.get('/dashboard/:id', (_req: Request, res: Response) => {
  const jobId = Array.isArray(_req.params.id) ? _req.params.id[0] : _req.params.id;

  res.setHeader('Content-Type', 'text/html');
  res.send(getDashboardHTML(jobId));
});

function getDashboardHTML(jobId: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Pipeline — ${jobId.slice(0, 8)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0f0f1a;--surface:#1a1a2e;--surface2:#16213e;--border:#2a2a4a;
  --text:#e0e0e0;--dim:#6a6a8a;--accent:#7c3aed;--accent2:#a78bfa;
  --green:#10b981;--red:#ef4444;--yellow:#f59e0b;--blue:#3b82f6;--cyan:#06b6d4;
}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:13px}
a{color:var(--accent2)}

/* Layout */
.app{display:grid;grid-template-rows:48px 1fr 28px;height:100vh}
.top-bar{display:flex;align-items:center;gap:16px;padding:0 16px;background:var(--surface);border-bottom:1px solid var(--border)}
.top-bar .job-id{color:var(--accent2);font-weight:700;font-size:14px}
.top-bar .repo{color:var(--dim);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:300px}
.top-bar .phase-badge{padding:2px 10px;border-radius:4px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.top-bar .elapsed{margin-left:auto;color:var(--dim);font-size:12px}

.main{display:grid;grid-template-columns:220px 1fr 240px;overflow:hidden}

/* Sidebar — phases */
.phases{background:var(--surface);border-right:1px solid var(--border);padding:16px 12px;overflow-y:auto}
.phases h3{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:12px}
.phase{display:flex;align-items:center;gap:8px;padding:8px;border-radius:6px;margin-bottom:4px;font-size:12px;color:var(--dim);position:relative}
.phase.active{background:var(--surface2);color:var(--accent2)}
.phase.done{color:var(--green)}
.phase.failed{color:var(--red)}
.phase .dot{width:8px;height:8px;border-radius:50%;background:var(--border);flex-shrink:0}
.phase.active .dot{background:var(--accent);box-shadow:0 0 8px var(--accent)}
.phase.done .dot{background:var(--green)}
.phase.failed .dot{background:var(--red)}

/* Event log */
.event-log{display:flex;flex-direction:column;overflow:hidden}
.event-log-header{padding:10px 16px;border-bottom:1px solid var(--border);font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);display:flex;align-items:center;gap:8px}
.event-log-header .count{color:var(--accent2)}
.events{flex:1;overflow-y:auto;padding:8px 16px;scroll-behavior:smooth}

.evt{padding:6px 0;border-bottom:1px solid rgba(42,42,74,0.3);display:grid;grid-template-columns:80px auto 1fr;gap:8px;align-items:start}
.evt .ts{color:var(--dim);font-size:11px;white-space:nowrap}
.evt .name{font-weight:600;font-size:12px;padding:1px 6px;border-radius:3px;white-space:nowrap}
.evt .summary{font-size:12px;color:var(--text);opacity:0.85}

/* Event type colors */
.evt .name.phase{background:var(--accent);color:#fff}
.evt .name.build{background:var(--blue);color:#fff}
.evt .name.deploy{background:var(--cyan);color:#fff}
.evt .name.verify{background:var(--green);color:#fff}
.evt .name.error{background:var(--red);color:#fff}
.evt .name.info{background:var(--surface2);color:var(--dim)}

.evt.error-row{background:rgba(239,68,68,0.08);border-radius:4px;padding:6px 8px}
.evt.verify-row{background:rgba(16,185,129,0.06);border-radius:4px;padding:6px 8px}
.evt.phase-row{border-bottom:1px solid var(--accent);padding-bottom:8px;margin-bottom:4px}
.evt.log-row{opacity:0.8;border-bottom:1px solid rgba(42,42,74,0.15)}
.evt.log-row .name{font-size:11px}
.evt.log-row .summary{font-size:11px;color:var(--dim)}

/* Stats sidebar */
.stats{background:var(--surface);border-left:1px solid var(--border);padding:16px 12px;overflow-y:auto}
.stats h3{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:12px}
.stat{margin-bottom:16px}
.stat .label{font-size:11px;color:var(--dim);margin-bottom:4px}
.stat .value{font-size:18px;font-weight:700;color:var(--accent2)}
.stat .value.sm{font-size:14px}

.progress-bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden;margin-top:6px}
.progress-bar .fill{height:100%;background:var(--accent);border-radius:3px;transition:width .3s}

/* Bottom bar */
.bottom-bar{display:flex;align-items:center;gap:12px;padding:0 16px;background:var(--surface);border-top:1px solid var(--border);font-size:11px;color:var(--dim)}
.bottom-bar .conn{display:flex;align-items:center;gap:4px}
.bottom-bar .conn .indicator{width:6px;height:6px;border-radius:50%}
.bottom-bar .conn .indicator.connected{background:var(--green)}
.bottom-bar .conn .indicator.disconnected{background:var(--red)}
.bottom-bar .last-event{margin-left:auto}

/* Agent cards */
.agent-cards{display:flex;flex-direction:column;gap:6px;margin-bottom:12px}
.agent-card{background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:11px}
.agent-card .agent-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.agent-card .agent-label{font-weight:700;color:var(--accent2)}
.agent-card .agent-model{color:var(--dim)}
.agent-card .agent-status{color:var(--dim);font-size:10px}
.agent-card.running{border-color:var(--accent)}
.agent-card.done{border-color:var(--green);opacity:0.7}
.agent-card.error{border-color:var(--red)}

/* E2E test grid */
.e2e-grid{margin-bottom:12px}
.e2e-grid h4{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:8px}
.e2e-cells{display:flex;flex-wrap:wrap;gap:3px}
.e2e-cell{width:14px;height:14px;border-radius:2px;background:var(--border);cursor:default;position:relative}
.e2e-cell.pass{background:var(--green)}
.e2e-cell.fail{background:var(--red)}
.e2e-cell.blocked{background:var(--yellow)}
.e2e-cell[title]:hover::after{content:attr(title);position:absolute;bottom:18px;left:50%;transform:translateX(-50%);background:var(--surface);border:1px solid var(--border);padding:2px 6px;border-radius:3px;font-size:10px;white-space:nowrap;z-index:10;color:var(--text)}
.e2e-summary{font-size:11px;color:var(--dim);margin-top:6px}

/* Auth overlay */
.auth-overlay{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:100}
.auth-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:32px;max-width:400px;width:100%}
.auth-box h2{margin-bottom:16px;font-size:16px;color:var(--accent2)}
.auth-box input{width:100%;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:inherit;font-size:13px;margin-bottom:12px}
.auth-box button{padding:10px 20px;background:var(--accent);color:#fff;border:none;border-radius:4px;font-family:inherit;font-size:13px;cursor:pointer}
.auth-box button:hover{opacity:0.9}
</style>
</head>
<body>

<div class="auth-overlay" id="authOverlay">
  <div class="auth-box">
    <h2>Pipeline Dashboard</h2>
    <p style="color:var(--dim);margin-bottom:16px;font-size:12px">Enter your API token to connect</p>
    <input type="password" id="tokenInput" placeholder="Bearer token" autofocus/>
    <button onclick="connectWithToken()">Connect</button>
  </div>
</div>

<div class="app" style="display:none" id="appContainer">
  <div class="top-bar">
    <span class="job-id" id="jobId">${jobId.slice(0, 8)}</span>
    <span class="repo" id="repoName">—</span>
    <span class="phase-badge" id="phaseBadge" style="background:var(--dim)">connecting</span>
    <span class="elapsed" id="elapsed">00:00</span>
  </div>

  <div class="main">
    <div class="phases">
      <h3>Pipeline</h3>
      <div class="phase" data-phase="init"><span class="dot"></span>Initializing</div>
      <div class="phase" data-phase="clone"><span class="dot"></span>Cloning</div>
      <div class="phase" data-phase="plan"><span class="dot"></span>Planning</div>
      <div class="phase" data-phase="build"><span class="dot"></span>Building</div>
      <div class="phase" data-phase="deploy"><span class="dot"></span>Deploying</div>
      <div class="phase" data-phase="seed"><span class="dot"></span>Seed Data</div>
      <div class="phase" data-phase="test"><span class="dot"></span>E2E Tests</div>
      <div class="phase" data-phase="fix"><span class="dot"></span>Fix Loop</div>
      <div class="phase" data-phase="done"><span class="dot"></span>Complete</div>
    </div>

    <div class="event-log">
      <div class="event-log-header">Event Log <span class="count" id="eventCount">0</span></div>
      <div class="events" id="eventList"></div>
    </div>

    <div class="stats">
      <h3>Stats</h3>
      <div class="agent-cards" id="agentCards"></div>
      <div class="e2e-grid" id="e2eGrid" style="display:none"><h4>E2E Tests</h4><div class="e2e-cells" id="e2eCells"></div><div class="e2e-summary" id="e2eSummary"></div></div>
      <div class="stat"><div class="label">Events</div><div class="value" id="statEvents">0</div></div>
      <div class="stat"><div class="label">Current Phase</div><div class="value sm" id="statPhase">—</div></div>
      <div class="stat"><div class="label">Cost</div><div class="value sm" id="statCost">—</div></div>
      <div class="stat"><div class="label">Turns</div><div class="value sm" id="statTurns">—</div></div>
      <div class="stat">
        <div class="label">Tasks</div>
        <div class="value sm" id="statTasks">—</div>
        <div class="progress-bar"><div class="fill" id="taskProgress" style="width:0%"></div></div>
      </div>
      <div class="stat"><div class="label">Verification</div><div class="value sm" id="statVerify">—</div></div>
    </div>
  </div>

  <div class="bottom-bar">
    <div class="conn"><span class="indicator disconnected" id="connIndicator"></span><span id="connStatus">Disconnected</span></div>
    <span class="last-event" id="lastEventTime">—</span>
  </div>
</div>

<script>
const JOB_ID = '${jobId}';
let TOKEN = '';
let eventSource = null;
let events = [];
let startTime = null;
let elapsedInterval = null;
let totalTasks = 0;
let completedTasks = 0;
let verifyPass = 0;
let verifyFail = 0;
let currentPhase = 'init';
let activeAgents = {};
let totalCostUsd = 0;
let e2eFlowResults = {};

const PHASE_MAP = {
  worker_started:'init', worker_launched:'init',
  repo_cloned:'clone',
  prd_parsed:'plan',
  orchestrator_started:'build', orchestrator_complete:'build',
  task_started:'build', task_completed:'build', task_failed:'build',
  deploy_started:'deploy', neon_provisioning:'deploy', schema_migrating:'deploy',
  flyio_deploying:'deploy', deploy_verifying:'deploy', deployed:'deploy',
  readiness_check:'deploy', readiness_passed:'deploy', readiness_failed:'deploy', readiness_fixing:'deploy',
  seeding_started:'seed', seeding_complete:'seed', seeding_skipped:'seed', seeding_failed:'seed',
  e2e_testing_started:'test', e2e_testing_complete:'test', e2e_testing_skipped:'test', e2e_testing_failed:'test',
  e2e_loop_started:'test', e2e_loop_complete:'test',
  e2e_fix_started:'fix', e2e_fix_failed:'fix',
  e2e_redeploy_started:'fix', e2e_redeploy_complete:'fix', e2e_redeploy_failed:'fix',
  agent_started:'build', agent_progress:'build', agent_completed:'build', agent_error:'build',
  e2e_batch_started:'test', e2e_batch_completed:'test', e2e_batch_failed:'test',
  e2e_flow_passed:'test', e2e_flow_failed:'test', e2e_flow_blocked:'test',
  generating_test_docs:'build',
  completed:'done', build_complete:'done',
  failed:'error', build_failed:'error',
};

const VERIFY_EVENTS = ['readiness_check','readiness_passed','readiness_failed','readiness_fixing'];

function getEventCategory(name, detail) {
  if (['failed','build_failed','error'].includes(name)) return 'error';
  if (['agent_started','agent_progress','agent_completed'].includes(name)) return 'build';
  if (name === 'agent_error') return 'error';
  if (['e2e_batch_started','e2e_batch_completed','e2e_batch_failed'].includes(name)) return 'verify';
  if (['e2e_flow_passed'].includes(name)) return 'verify';
  if (['e2e_flow_failed','e2e_flow_blocked'].includes(name)) return 'error';
  if (VERIFY_EVENTS.includes(name)) return 'verify';
  if (['deployed','completed','build_complete'].includes(name)) return 'deploy';
  if (['seeding_started','seeding_complete','seeding_skipped','seeding_failed'].includes(name)) return 'phase';
  if (['e2e_testing_started','e2e_testing_complete','e2e_loop_started','e2e_loop_complete'].includes(name)) return 'verify';
  if (['e2e_testing_failed','e2e_fix_started','e2e_fix_failed','e2e_redeploy_started','e2e_redeploy_complete','e2e_redeploy_failed'].includes(name)) return 'build';
  if (['task_started','task_completed','task_failed','orchestrator_started','orchestrator_complete'].includes(name)) return 'build';
  if (['worker_started','worker_launched','repo_cloned','prd_parsed'].includes(name)) return 'phase';
  if (name === 'log' && detail?.type === 'tool_use') return 'build';
  if (name === 'log') return 'info';
  return 'info';
}

function summarize(evt) {
  const d = evt.detail || {};
  const name = evt.event;
  switch(name) {
    case 'worker_started': return 'Worker process started';
    case 'worker_launched': return 'Worker launched on Cloud Run';
    case 'repo_cloned': return 'Repository cloned' + (d.repo_url ? ': ' + d.repo_url.split('/').pop() : '');
    case 'prd_parsed': return 'PRD parsed — ' + (d.total_tasks || '?') + ' tasks identified';
    case 'orchestrator_started': return 'Orchestrator started building';
    case 'orchestrator_complete': return 'Build done — $' + (d.cost_usd?.toFixed(2) || '?') + ', ' + (d.turns || '?') + ' turns';
    case 'task_started': return 'Task ' + (d.task_number || '?') + ': ' + (d.task_name || d.description || 'starting');
    case 'task_completed': return 'Task ' + (d.task_number || '?') + ' completed';
    case 'task_failed': return 'Task ' + (d.task_number || '?') + ' failed' + (d.error_preview ? ': ' + d.error_preview : '');
    case 'deploy_started': return 'Deployment started';
    case 'neon_provisioning': return 'Provisioning Neon database';
    case 'schema_migrating': return 'Running database migrations';
    case 'readiness_check': return 'Running readiness check' + (d.attempt ? ' (attempt ' + d.attempt + ')' : '');
    case 'readiness_passed': return 'Readiness check passed';
    case 'readiness_failed': return 'Readiness check failed' + (d.error_preview ? ': ' + d.error_preview : '');
    case 'readiness_fixing': return 'Fix attempt ' + (d.attempt || '?') + (d.error_preview ? ': ' + d.error_preview : '');
    case 'flyio_deploying': return 'Deploying to Fly.io';
    case 'deploy_verifying': return 'Verifying deployment';
    case 'deployed': return 'Deployed' + (d.live_url ? ' — ' + d.live_url : '');
    case 'seeding_started': return 'Seeding test data...';
    case 'seeding_complete': return 'Test data seeded successfully';
    case 'seeding_skipped': return 'Seeding skipped' + (d.reason ? ': ' + d.reason : '');
    case 'seeding_failed': return 'Seeding failed' + (d.error ? ': ' + d.error : '');
    case 'e2e_testing_started': return 'Starting E2E tests (' + (d.mode || 'full') + ')';
    case 'e2e_testing_complete': return 'E2E: ' + (d.passed || 0) + '/' + (d.total || 0) + ' passed, ' + (d.failed || 0) + ' failed' + (d.all_passed ? ' ✓' : '');
    case 'e2e_testing_skipped': return 'E2E tests skipped' + (d.reason ? ': ' + d.reason : '');
    case 'e2e_testing_failed': return 'E2E testing failed' + (d.error ? ': ' + d.error : '');
    case 'e2e_loop_started': return 'Fix-retest loop (max ' + (d.max_iterations || '?') + ' iterations)';
    case 'e2e_loop_complete': return 'E2E loop done after ' + (d.iterations || '?') + ' iterations: ' + (d.result || '?');
    case 'e2e_fix_started': return 'Fixing ' + (d.failed_flows?.length || 0) + ' failed flows (iteration ' + (d.iteration || '?') + ')';
    case 'e2e_fix_failed': return 'Fix attempt failed (iteration ' + (d.iteration || '?') + ')';
    case 'e2e_redeploy_started': return 'Redeploying (iteration ' + (d.iteration || '?') + ')';
    case 'e2e_redeploy_complete': return 'Redeploy complete (iteration ' + (d.iteration || '?') + ')';
    case 'e2e_redeploy_failed': return 'Redeploy failed (iteration ' + (d.iteration || '?') + ')';
    case 'agent_started': return (d.agent_label || 'agent') + ' started (' + (d.model || '?') + ', max ' + (d.max_turns || '?') + ' turns)';
    case 'agent_progress': return (d.agent_label || 'agent') + ' turn ' + (d.turn || '?') + ' — tools: ' + (d.tools_used?.join(', ') || 'none');
    case 'agent_completed': return (d.agent_label || 'agent') + ' done — ' + (d.turns || '?') + ' turns, $' + (d.cost_usd?.toFixed(2) || '?') + ', ' + ((d.duration_ms/1000)?.toFixed(0) || '?') + 's';
    case 'agent_error': return (d.agent_label || 'agent') + ' error: ' + (d.error || '?');
    case 'e2e_batch_started': return 'E2E batch ' + ((d.batch_idx ?? 0) + 1) + '/' + (d.total_batches || '?') + ' started (' + (d.flow_count || '?') + ' flows)';
    case 'e2e_batch_completed': return 'E2E batch ' + ((d.batch_idx ?? 0) + 1) + ' done — ' + (d.passed || 0) + ' pass, ' + (d.failed || 0) + ' fail, ' + (d.blocked || 0) + ' blocked';
    case 'e2e_batch_failed': return 'E2E batch ' + ((d.batch_idx ?? 0) + 1) + ' error: ' + (d.error || '?');
    case 'e2e_flow_passed': return 'PASS: ' + (d.flow_id || '?');
    case 'e2e_flow_failed': return 'FAIL: ' + (d.flow_id || '?') + (d.error ? ' — ' + d.error : '');
    case 'e2e_flow_blocked': return 'BLOCKED: ' + (d.flow_id || '?') + (d.reason ? ' — ' + d.reason : '');
    case 'generating_test_docs': return 'Generating missing test docs: ' + (d.missing?.join(', ') || '?');
    case 'pr_created': return 'PR created' + (d.pr_url ? ' — ' + d.pr_url : '');
    case 'completed': case 'build_complete': return 'Pipeline completed successfully';
    case 'failed': case 'build_failed': return 'Pipeline failed' + (d.message ? ': ' + d.message : '');
    case 'stream_end': return 'Stream ended — final status: ' + (d.final_status || '?');
    case 'log':
      if (d.type === 'tool_use') return 'Tool: ' + (d.tool || '?') + (d.turn ? ' (turn ' + d.turn + ')' : '');
      if (d.type === 'text') {
        const t = d.text || '';
        return t.length > 120 ? t.slice(0, 120) + '...' : t;
      }
      return d.text || d.message || 'log';
    default: return d.message || name.replace(/_/g, ' ');
  }
}

function formatTime(date) {
  const d = new Date(date);
  return d.toLocaleTimeString('en-GB', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}

function formatElapsed(ms) {
  const s = Math.floor(ms/1000);
  const m = Math.floor(s/60);
  const h = Math.floor(m/60);
  if (h > 0) return h + ':' + String(m%60).padStart(2,'0') + ':' + String(s%60).padStart(2,'0');
  return String(m).padStart(2,'0') + ':' + String(s%60).padStart(2,'0');
}

function updatePhases(phase) {
  currentPhase = phase;
  const order = ['init','clone','plan','build','deploy','seed','test','fix','done'];
  const idx = order.indexOf(phase);
  document.querySelectorAll('.phase').forEach(el => {
    const p = el.dataset.phase;
    const pi = order.indexOf(p);
    el.classList.remove('active','done','failed');
    if (phase === 'error') {
      if (pi < idx || pi <= order.indexOf(currentPhase)) el.classList.add('done');
      if (p === currentPhase) el.classList.add('failed');
    } else if (pi < idx) {
      el.classList.add('done');
    } else if (pi === idx) {
      el.classList.add('active');
    }
  });
  document.getElementById('statPhase').textContent = phase;
  const badge = document.getElementById('phaseBadge');
  badge.textContent = phase;
  const colors = {init:'var(--dim)',clone:'var(--blue)',plan:'var(--cyan)',build:'var(--accent)',deploy:'var(--yellow)',seed:'var(--cyan)',test:'var(--green)',fix:'var(--yellow)',done:'var(--green)',error:'var(--red)'};
  badge.style.background = colors[phase] || 'var(--dim)';
}

function addEvent(evt) {
  events.push(evt);
  const cat = getEventCategory(evt.event, evt.detail);
  const phase = PHASE_MAP[evt.event];
  if (phase) updatePhases(phase);

  // Track stats
  if (evt.event === 'prd_parsed' && evt.detail?.total_tasks) totalTasks = evt.detail.total_tasks;
  if (evt.event === 'task_completed') completedTasks++;
  if (evt.event === 'task_started' && evt.detail?.task_number > completedTasks) completedTasks = evt.detail.task_number - 1;
  if (evt.event === 'readiness_passed') verifyPass++;
  if (evt.event === 'readiness_failed') verifyFail++;
  // Agent card tracking
  if (evt.event === 'agent_started') {
    const label = evt.detail?.agent_label || 'agent';
    activeAgents[label] = {model: evt.detail?.model, turns: 0, status: 'running'};
    renderAgentCards();
  }
  if (evt.event === 'agent_progress') {
    const label = evt.detail?.agent_label || 'agent';
    if (activeAgents[label]) { activeAgents[label].turns = evt.detail?.turn || 0; activeAgents[label].tools = evt.detail?.tools_used; activeAgents[label].text = evt.detail?.last_text; }
    renderAgentCards();
  }
  if (evt.event === 'agent_completed') {
    const label = evt.detail?.agent_label || 'agent';
    if (activeAgents[label]) { activeAgents[label].status = 'done'; activeAgents[label].turns = evt.detail?.turns || 0; activeAgents[label].cost = evt.detail?.cost_usd; activeAgents[label].duration = evt.detail?.duration_ms; }
    if (evt.detail?.cost_usd) totalCostUsd += evt.detail.cost_usd;
    document.getElementById('statCost').textContent = '$' + totalCostUsd.toFixed(2);
    renderAgentCards();
  }
  if (evt.event === 'agent_error') {
    const label = evt.detail?.agent_label || 'agent';
    if (activeAgents[label]) { activeAgents[label].status = 'error'; activeAgents[label].error = evt.detail?.error; }
    renderAgentCards();
  }

  // E2E flow tracking
  if (['e2e_flow_passed','e2e_flow_failed','e2e_flow_blocked'].includes(evt.event)) {
    const fid = evt.detail?.flow_id || '?';
    const status = evt.event === 'e2e_flow_passed' ? 'pass' : evt.event === 'e2e_flow_failed' ? 'fail' : 'blocked';
    e2eFlowResults[fid] = {status, error: evt.detail?.error || evt.detail?.reason || ''};
    renderE2eGrid();
  }

  // Update DOM
  document.getElementById('statEvents').textContent = events.length;
  document.getElementById('eventCount').textContent = events.length;
  if (totalTasks > 0) {
    document.getElementById('statTasks').textContent = completedTasks + '/' + totalTasks;
    document.getElementById('taskProgress').style.width = Math.round((completedTasks/totalTasks)*100) + '%';
  }
  if (verifyPass + verifyFail > 0) {
    document.getElementById('statVerify').textContent = verifyPass + ' pass / ' + verifyFail + ' fail';
  }
  document.getElementById('lastEventTime').textContent = 'Last: ' + formatTime(evt.created_at);

  // Render event row
  const el = document.createElement('div');
  const isLog = evt.event === 'log';
  el.className = 'evt' + (cat === 'error' ? ' error-row' : cat === 'verify' ? ' verify-row' : phase ? ' phase-row' : isLog ? ' log-row' : '');
  const displayName = evt.event === 'log' ? (evt.detail?.type === 'tool_use' ? 'tool' : 'agent') : evt.event;
  el.innerHTML = '<span class="ts">' + formatTime(evt.created_at) + '</span>'
    + '<span class="name ' + cat + '">' + displayName + '</span>'
    + '<span class="summary">' + summarize(evt) + '</span>';
  const list = document.getElementById('eventList');
  list.appendChild(el);
  list.scrollTop = list.scrollHeight;

  if (!startTime) startTime = new Date(evt.created_at);
}

function renderAgentCards() {
  const container = document.getElementById('agentCards');
  container.innerHTML = '';
  for (const [label, info] of Object.entries(activeAgents)) {
    const card = document.createElement('div');
    card.className = 'agent-card ' + info.status;
    let statusText = '';
    if (info.status === 'running') statusText = 'Turn ' + (info.turns || 0) + (info.tools ? ' — ' + info.tools.slice(-3).join(', ') : '');
    else if (info.status === 'done') statusText = info.turns + ' turns, $' + (info.cost?.toFixed(2) || '?') + ', ' + ((info.duration/1000)?.toFixed(0) || '?') + 's';
    else if (info.status === 'error') statusText = 'Error: ' + (info.error?.slice(0, 80) || '?');
    card.innerHTML = '<div class="agent-header"><span class="agent-label">' + label + '</span><span class="agent-model">' + (info.model || '') + '</span></div><div class="agent-status">' + statusText + '</div>';
    container.appendChild(card);
  }
}

function renderE2eGrid() {
  const grid = document.getElementById('e2eGrid');
  const cells = document.getElementById('e2eCells');
  const summary = document.getElementById('e2eSummary');
  grid.style.display = 'block';
  cells.innerHTML = '';
  let pass = 0, fail = 0, blocked = 0;
  for (const [fid, info] of Object.entries(e2eFlowResults)) {
    const cell = document.createElement('div');
    cell.className = 'e2e-cell ' + info.status;
    cell.title = fid + (info.error ? ': ' + info.error : '');
    cells.appendChild(cell);
    if (info.status === 'pass') pass++;
    else if (info.status === 'fail') fail++;
    else blocked++;
  }
  summary.textContent = pass + ' pass / ' + fail + ' fail / ' + blocked + ' blocked';
}

function connect() {
  const url = new URL('/jobs/' + JOB_ID + '/stream', location.origin);
  url.searchParams.set('token', TOKEN);
  eventSource = new EventSource(url.toString());

  eventSource.onopen = () => {
    document.getElementById('connIndicator').className = 'indicator connected';
    document.getElementById('connStatus').textContent = 'Connected';
  };
  eventSource.onmessage = (e) => {
    try { addEvent(JSON.parse(e.data)); } catch {}
  };
  eventSource.onerror = () => {
    document.getElementById('connIndicator').className = 'indicator disconnected';
    document.getElementById('connStatus').textContent = 'Reconnecting...';
  };
}

function connectWithToken() {
  TOKEN = document.getElementById('tokenInput').value.trim();
  if (!TOKEN) return;

  // First load full job data
  fetch('/jobs/' + JOB_ID + '/full', {headers:{'Authorization':'Bearer ' + TOKEN}})
    .then(r => {
      if (!r.ok) throw new Error('Auth failed');
      return r.json();
    })
    .then(data => {
      document.getElementById('authOverlay').style.display = 'none';
      document.getElementById('appContainer').style.display = 'grid';

      // Populate header
      const repo = data.repo_url || '';
      document.getElementById('repoName').textContent = repo.replace('https://github.com/','');
      document.getElementById('jobId').textContent = data.id?.slice(0,8) || JOB_ID.slice(0,8);

      startTime = new Date(data.created_at);
      elapsedInterval = setInterval(() => {
        document.getElementById('elapsed').textContent = formatElapsed(Date.now() - startTime.getTime());
      }, 1000);

      // Connect SSE
      connect();
    })
    .catch(() => {
      document.getElementById('tokenInput').style.borderColor = 'var(--red)';
      document.getElementById('tokenInput').placeholder = 'Invalid token — try again';
      document.getElementById('tokenInput').value = '';
    });
}

// Auto-connect if token in URL
const urlToken = new URLSearchParams(location.search).get('token');
if (urlToken) {
  TOKEN = urlToken;
  document.getElementById('tokenInput').value = urlToken;
  connectWithToken();
}
</script>
</body>
</html>`;
}
