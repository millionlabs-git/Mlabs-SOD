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
    return f"""\
Deploy this project to Netlify:

1. **Detect required environment variables** before deploying:
   - Read `.env.example`, `.env.local.example`, `.env.sample`, or similar template files
   - Scan the codebase for `process.env.*` or `import.meta.env.*` references
   - Check framework config files (next.config.*, nuxt.config.*, etc.)
   - Cross-reference with any docs/PRD.md requirements

2. **Resolve each env var** using this priority:
   - **Known values** (set these exactly):{env_vars_hint}
   - **Auto-generate** secrets that just need a random value:
     - `NEXTAUTH_SECRET`, `JWT_SECRET`, `SESSION_SECRET`, `SECRET_KEY`, etc. → generate a 64-char hex string using `openssl rand -hex 32`
   - **Derive from deployment** — set after you know the site URL:
     - `NEXTAUTH_URL`, `NEXT_PUBLIC_URL`, `APP_URL`, `BASE_URL` → use the Netlify site URL
     - `NEXT_PUBLIC_API_URL` → use the Netlify site URL + `/api`
   - **Skip safely** — vars that are optional or only needed in dev:
     - `NODE_ENV` (Netlify sets this automatically)
     - Analytics keys, logging tokens, dev-only flags
   - **Flag as missing** — vars that need real third-party credentials you can't generate:
     - Payment keys (STRIPE_*, PAYPAL_*), OAuth credentials (GOOGLE_CLIENT_*, GITHUB_CLIENT_*), external API keys
     - List these in the deployment info so the user knows to set them manually

3. Use the Netlify MCP tool to create a new site named "sod-{job_id[:8]}"

4. Set ALL resolved environment variables on the Netlify site using the MCP tool

5. Detect the build output directory:
   - Next.js: `out/` (static export) or `.next/` (SSR)
   - Vite/React: `dist/`
   - Create React App: `build/`
   - Check package.json scripts and framework config

6. Deploy the build output to Netlify

7. Save the deployment info to /tmp/netlify-deployment.json:
   {{
     "site_id": "<netlify_site_id>",
     "site_url": "<deployed_url>",
     "deploy_id": "<deploy_id>",
     "env_vars_set": ["DATABASE_URL", "NEXTAUTH_SECRET", ...],
     "env_vars_missing": ["STRIPE_SECRET_KEY", ...]
   }}

8. Print the live URL and any missing env vars that need manual setup.

If the deploy fails, check the build output directory and retry.
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
