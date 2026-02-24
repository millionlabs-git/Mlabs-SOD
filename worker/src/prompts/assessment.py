from __future__ import annotations


def maturity_assessment_prompt(prd_content: str) -> str:
    return f"""\
You are a codebase maturity assessor. Your job is to compare the current codebase \
against the PRD and determine how much of the application has already been built.

## PRD
{prd_content}

## Instructions

Analyze the codebase thoroughly:

1. **Project structure** — Does it have a proper project setup? (package.json, \
config files, directory structure, dependencies installed)

2. **Architecture** — Are the major components from the PRD implemented? \
Check for routes, pages, API endpoints, database models, services.

3. **Features** — For each feature/requirement in the PRD, check if the \
implementation exists:
   - Read the relevant source files
   - Check if the feature logic is actually implemented (not just stubs/TODOs)
   - Note any features that are partially implemented

4. **Database** — If the PRD requires a database:
   - Check for schema files (prisma/schema.prisma, drizzle/, schema.sql, migrations/)
   - Verify the schema covers the data models from the PRD

5. **Build** — Try running the build command (`npm run build` or equivalent):
   - Does it succeed?
   - Are there TypeScript/compilation errors?

6. **Tests** — Check if tests exist and try running them

Based on your analysis, write a JSON assessment to /tmp/assessment.json:

```json
{{
  "planning_complete": true/false,
  "scaffolding_complete": true/false,
  "building_complete": true/false,
  "review_complete": true/false,
  "build_succeeds": true/false,
  "feature_coverage": 0.0-1.0,
  "needs_fixes": ["list of issues found"],
  "missing_features": ["features from PRD not yet implemented"],
  "summary": "one paragraph assessment"
}}
```

Decision criteria:
- **planning_complete**: true if the project has a clear structure and architecture \
(even without docs/ARCHITECTURE.md — the code itself demonstrates architecture)
- **scaffolding_complete**: true if project skeleton exists (package.json, directory \
structure, configs, dependencies)
- **building_complete**: true if 80%+ of PRD features are implemented and the \
build succeeds. Set false if major features are missing or build fails.
- **review_complete**: true if the code is production-quality (no obvious bugs, \
proper error handling, reasonable test coverage). Usually false for externally-built \
apps that haven't been through our review pipeline.

Be thorough but practical. A working app with minor gaps is "building_complete". \
An app missing core features or that doesn't build is not.
"""
