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

For Next.js static export projects, check if `next.config.*` has `output: 'export'`. \
If not, and the project is a simple SPA/static site, add it so Netlify can serve it.
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


def netlify_deploy_prompt(job_id: str, env_vars_hint: str) -> str:
    site_name = f"sod-{job_id[:8]}"
    return f"""\
Deploy this project to Netlify using the Netlify CLI.

## Step 1: Detect the build output directory

Check which directory contains the production build output:
- Next.js static export: `out/`
- Next.js SSR: `.next/`
- Vite/React: `dist/`
- Create React App: `build/`
- Read package.json and framework config to confirm

## Step 2: Create site and deploy using the Netlify CLI

Run these commands:

```bash
# Create a new site
npx netlify-cli sites:create --name "{site_name}" --account-slug "" --json || true

# Deploy the build output (replace BUILD_DIR with actual directory)
npx netlify-cli deploy --prod --dir=BUILD_DIR --site "{site_name}" --json
```

If the site name is taken, try `{site_name}-app` or `{site_name}-live`.

The `--json` flag returns structured output. Parse it to get the site URL and ID.

## Step 3: Detect and set environment variables

After deploying, set env vars on the site:{env_vars_hint}

Also detect required env vars:
- Read `.env.example` or similar template files
- Scan for `process.env.*` or `import.meta.env.*` references
- **Auto-generate** secrets: `NEXTAUTH_SECRET`, `JWT_SECRET`, `SESSION_SECRET` → `openssl rand -hex 32`
- **Derive from site URL**: `NEXTAUTH_URL`, `APP_URL`, `BASE_URL` → use the deployed URL
- **Flag as missing**: third-party keys (STRIPE_*, OAuth, external APIs)

Set env vars via CLI:
```bash
npx netlify-cli env:set VAR_NAME "value" --site "{site_name}"
```

## Step 4: Save deployment info

Write to /tmp/netlify-deployment.json:
```json
{{
  "site_id": "<from deploy output>",
  "site_url": "<from deploy output>",
  "deploy_id": "<from deploy output>",
  "env_vars_set": ["list of vars set"],
  "env_vars_missing": ["list of vars that need manual setup"]
}}
```

IMPORTANT: You MUST write this file. The pipeline reads it to report the live URL.

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
   - Home page renders correctly (not a blank page, error, or default Netlify page)
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
