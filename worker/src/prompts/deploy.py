from __future__ import annotations


def neon_provision_prompt(job_id: str) -> str:
    return f"""\
Provision a new Neon Postgres database for this project:

1. Use the Neon MCP tool `create_project` to create a new project named "sod-{job_id[:8]}"
2. Use `get_connection_string` to retrieve the database URL
3. Save the credentials to /tmp/neon-credentials.json with this format:
   {{
     "project_id": "<neon_project_id>",
     "database_url": "<connection_string>",
     "host": "<host>",
     "database": "<database_name>"
   }}
4. Print the project ID when done

Do NOT create any tables yet — schema migration is handled separately.
"""


def schema_migration_prompt(db_url: str) -> str:
    return f"""\
Run the database schema migration against the provisioned Neon database.

Database URL: {db_url}

Detect the schema management approach used in this project:
1. **Prisma** — If `prisma/schema.prisma` exists, run:
   DATABASE_URL="{db_url}" npx prisma db push
2. **Drizzle** — If `drizzle/` or `drizzle.config.*` exists, run:
   DATABASE_URL="{db_url}" npx drizzle-kit push
3. **Raw SQL** — If `schema.sql`, `migrations/`, or `db/migrate/` exists, run:
   psql "{db_url}" -f <schema_file>
4. **No schema found** — If none of the above exist, skip migration and report \
"no schema files detected".

Set the DATABASE_URL environment variable in any .env or .env.local file so the \
app can connect at runtime.

Report what migration approach was used and whether it succeeded.
"""


def production_build_prompt(db_url: str | None) -> str:
    env_hint = ""
    if db_url:
        env_hint = f"""
Ensure the following environment variable is set in .env or .env.local before building:
  DATABASE_URL="{db_url}"
"""
    return f"""\
Build the project for production deployment:{env_hint}

1. Read package.json (or equivalent) to understand the build command
2. Run `npm run build` (or the appropriate build command)
3. If the build fails, diagnose and fix the errors, then retry
4. Verify the build output directory exists (typically `dist/`, `build/`, `.next/`, or `out/`)
5. Report the build output directory path
"""


def build_fix_prompt(errors: str, attempt: int, max_retries: int) -> str:
    return f"""\
The production build failed (attempt {attempt}/{max_retries}). Diagnose and fix the errors.

## Build errors:
```
{errors}
```

## Instructions:

1. Read the error output carefully and identify the root cause
2. Common issues to check:
   - Missing dependencies → run `npm install <package>`
   - TypeScript errors → fix the type issues in the source files
   - Missing environment variables → add defaults or mock values for build time
   - Import errors → fix import paths or install missing modules
   - Next.js config issues → check next.config.js/ts settings
   - ESLint errors blocking build → fix the lint issues or adjust config
3. Fix ALL errors you find, not just the first one
4. Do NOT run `npm run build` yourself — the system will retry automatically after your fixes
5. If the project needs specific Node.js version or other system deps, note it but try to work around it

Be thorough — this is attempt {attempt} of {max_retries}. Fix everything you can find.
"""


def flyio_deploy_prompt(job_id: str, db_url: str | None) -> str:
    app_name = f"sod-{job_id[:8]}"

    db_secret_hint = ""
    if db_url:
        db_secret_hint = f'\nflyctl secrets set DATABASE_URL="{db_url}" -a {app_name}'

    return f"""\
Deploy this full-stack project to Fly.io as a single container.

## Step 1: Analyse the project and find the Dockerfile

1. Check if a `Dockerfile` already exists in the repo root
2. Identify the backend server entry point and the port it listens on (check server source code, \
Dockerfile EXPOSE, or .env files — do NOT assume 3000)
3. Identify the frontend build output directory

**CRITICAL: If a Dockerfile already exists, USE IT as-is. Do NOT generate a new one. \
Only create a Dockerfile if none exists in the project.**

## Step 2: Generate a Dockerfile ONLY if none exists

Skip this step entirely if a Dockerfile was found in Step 1.

If no Dockerfile exists, create one. For a typical full-stack Node.js app:

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY --from=builder /app/<build-output> ./<build-output>
COPY --from=builder /app/<server-files> ./<server-files>
EXPOSE <PORT>
CMD ["node", "<server-entry-point>"]
```

Adapt based on the project:
- For monorepos with `frontend/` and `backend/` dirs, copy both build outputs
- Ensure the backend serves the frontend static files in production
- Use `--ignore-scripts` in production npm ci to avoid devDependency scripts (husky, etc.)

## Step 3: Generate fly.toml

Detect the port from Step 1 and create `fly.toml`:
```toml
app = "{app_name}"
primary_region = "lhr"

[build]

[env]
  NODE_ENV = "production"
  PORT = "<detected-port>"

[http_service]
  internal_port = <detected-port>
  force_https = true
  auto_stop_machines = "stop"
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  memory = "512mb"
  cpu_kind = "shared"
  cpus = 1
```

## Step 4: Create Fly app and set ALL secrets BEFORE deploying

Create the app first:
```bash
flyctl apps create {app_name} --org personal || true
```

If the name is taken, try `{app_name}-app` or `{app_name}-live` and update fly.toml.

Now set secrets — the app MUST have these before the first deploy or it will crash on startup:
{db_secret_hint}

Detect and set other required env vars:
- Read `.env.example` or similar template files
- Scan for `process.env.*` or `import.meta.env.*` references
- **Auto-generate** secrets: `JWT_SECRET`, `SESSION_SECRET`, `NEXTAUTH_SECRET` → `openssl rand -hex 32`
- **Derive from app URL**: `APP_URL`, `BASE_URL`, `NEXTAUTH_URL` → `https://{app_name}.fly.dev`
- **Flag as missing**: third-party keys (STRIPE_*, OAuth, external APIs) — set placeholder values \
like "CHANGE_ME" so the app can at least start

Set all secrets in one command:
```bash
flyctl secrets set KEY1="val1" KEY2="val2" ... -a {app_name}
```

## Step 5: Deploy to Fly.io

```bash
flyctl deploy -a {app_name}
```

If the deploy fails, read the error, fix the Dockerfile or config, and retry.

## Step 6: Verify the app is reachable

```bash
# Wait for the app to start, then check it responds
sleep 10
curl -s -o /dev/null -w "%{{http_code}}" https://{app_name}.fly.dev
```

If you get 000 or 502, check logs with `flyctl logs -a {app_name}` and fix the issue.

## Step 7: Write deployment info file

**YOU MUST DO THIS — the pipeline will fail if this file is missing.**

```bash
cat > /tmp/fly-deployment.json << 'DEPLOY_EOF'
{{
  "app_name": "{app_name}",
  "app_url": "https://{app_name}.fly.dev",
  "env_vars_set": [],
  "env_vars_missing": []
}}
DEPLOY_EOF
```

Update the `env_vars_set` and `env_vars_missing` arrays with the actual values from Step 4.

**Write this file BEFORE doing anything else at the end. This is NOT optional.**

Print the live URL when done.
"""


def deployment_verify_prompt(
    site_url: str,
    vp_script: str,
    screenshots_dir: str,
    has_db: bool,
) -> str:
    db_check = ""
    if has_db:
        db_check = """
- Verify database-dependent pages load data (not empty states or connection errors)
- Check that API routes return valid responses"""

    return f"""\
Verify the live deployment at {site_url} is working correctly:

1. Use Visual Playwright to visit the live site and take screenshots:
   node {vp_script} goto "{site_url}" --screenshot {screenshots_dir}/deploy-home.png

2. Check the following:
   - Home page renders correctly (not a blank page, error, or default placeholder page)
   - Navigation links work
   - Key pages from the PRD are accessible{db_check}

3. Take screenshots of 2-3 key pages and save to {screenshots_dir}/

4. Write a brief deployment verification report to docs/DEPLOYMENT.md with:
   - Live URL: {site_url}
   - Verification status (pass/fail)
   - Screenshots taken
   - Any issues found

5. Close Visual Playwright sessions:
   node {vp_script} close

Report pass or fail with details.
"""
