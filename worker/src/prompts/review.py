from __future__ import annotations


def code_review_prompt() -> str:
    return """\
Review the entire codebase for:
1. Code quality and maintainability
2. Error handling completeness
3. Test coverage gaps
4. Performance concerns
5. Security vulnerabilities

Write your review to docs/CODE_REVIEW.md with specific file:line references \
for any issues found.

For critical issues, fix them directly.
For minor issues, document them in the review.
"""


def security_review_prompt() -> str:
    return """\
Perform a security review of the codebase. Check for:
- Hardcoded secrets or credentials
- Injection vulnerabilities (SQL, command, XSS)
- Authentication and authorization issues
- Dependency vulnerabilities (run npm audit / pip audit if applicable)
- Insecure configurations
- Missing input validation at system boundaries

Append findings to docs/CODE_REVIEW.md under a "## Security Review" section.
Fix any critical security issues directly.
"""


def visual_e2e_prompt(vp_script: str, e2e_dir: str) -> str:
    return f"""\
Perform a full visual E2E walkthrough of the application:

1. Read the PRD and architecture docs to understand all routes and pages
2. Start the dev server (npm run dev / npm start / python manage.py runserver / etc.)
3. Wait for the server to be ready
4. Use Visual Playwright to systematically visit every page:

   node {vp_script} goto "http://localhost:3000" \\
       --screenshot {e2e_dir}/home.png

   Repeat for each route in the application.

5. For each page, evaluate:
   - Does it render correctly? (no blank pages, no error screens)
   - Does the layout look reasonable?
   - Are navigation links working?
   - Are images and assets loading?

6. Test key user flows end-to-end:
   - Authentication flows (if applicable)
   - Main CRUD operations
   - Form submissions
   - Any interactive features from the PRD

7. For any issues found:
   - Screenshot the problem
   - Fix it if possible
   - Document it in docs/CODE_REVIEW.md if not fixable

8. Generate a visual summary at docs/VISUAL_REVIEW.md listing each \
page/flow tested with relative paths to screenshots and pass/fail status.

9. Close all Visual Playwright sessions and stop the dev server:
   node {vp_script} close

Keep all screenshots in {e2e_dir}/ — they will be included in the PR.
"""


def pr_description_prompt() -> str:
    return """\
Generate a comprehensive PR description that:

1. Summarizes what was built (1-2 paragraph overview)
2. Maps each PRD requirement to the implementing code
3. Lists architectural decisions and rationale
4. Notes any deferred items or known limitations
5. Includes test coverage stats (run the test suite and report)
6. References key screenshots from docs/screenshots/ as visual evidence:
   - E2E screenshots from docs/screenshots/e2e/
   - Per-task screenshots from docs/screenshots/task-*/
   - Use relative markdown image syntax: ![Page Name](docs/screenshots/e2e/home.png)
7. Include the visual review summary from docs/VISUAL_REVIEW.md (if it exists)

Read the PRD, architecture doc, build plan, code review, and visual review.
Write the PR description to docs/PR_DESCRIPTION.md.
"""
