"""Evaluator prompt templates for phase assessments."""
from __future__ import annotations


def evaluate_architecture_prompt(context: str) -> str:
    """Prompt that asks an agent to evaluate architecture against the PRD."""
    return f"""You are an architecture evaluator. Your job is to assess whether the architecture document fully and correctly addresses the product requirements.

{context}

Perform the following checks:

1. **Feature coverage** — Does the architecture address every feature listed in the PRD? List any missing features.
2. **Data models** — Are all database tables defined with their columns, types, and relations? Are there missing tables or columns for any feature?
3. **API endpoints** — Are API endpoints defined for every feature that needs them? Are request/response shapes specified?
4. **Tech stack** — Is the chosen tech stack reasonable for the requirements (performance, scale, complexity)?
5. **Completeness** — Are there clear component boundaries, data flow descriptions, and deployment considerations?

Scoring guide:
- 1.0: Architecture is comprehensive, all features covered, models complete, endpoints defined
- 0.8-0.9: Minor gaps (e.g. a missing column, an edge-case endpoint not defined)
- 0.7: Acceptable but has notable gaps that should be addressed
- 0.5-0.6: Significant gaps — missing tables, undefined endpoints, unclear data flow
- Below 0.5: Major problems — features missing, wrong tech choices, incomplete models

A score >= 0.7 passes. Below 0.7 fails and should be retried.

If the score is below 0.7, set recommendation to "retry_with_guidance" and provide specific instructions in the guidance field explaining exactly what needs to be fixed.

Respond with ONLY the following JSON object. Do not include any text before or after the JSON. Do not wrap it in markdown code fences.

{{"passed": true, "score": 0.85, "issues": ["list of specific problems found"], "recommendation": "proceed", "guidance": ""}}"""


def evaluate_scaffold_prompt(context: str) -> str:
    """Prompt that asks an agent to evaluate scaffold quality."""
    return f"""You are a scaffold evaluator. Your job is to assess whether the project scaffolding is correct, buildable, and matches the architecture.

{context}

Perform the following checks:

1. **Build verification** — Run the project's build command (e.g. `npm run build`, `npx tsc --noEmit`, or equivalent). Does it succeed without errors?
2. **Directory structure** — Does the file/folder layout match what the architecture document specifies? Are all expected directories present?
3. **Dependencies** — Are all packages and dependencies listed in the architecture actually installed? Check package.json, requirements.txt, or equivalent.
4. **Placeholder code** — Is there excessive TODO or placeholder code? Some TODOs are acceptable in a scaffold, but core structure should be real.
5. **Configuration** — Are config files (tsconfig.json, eslint, prettier, .env.example, etc.) properly set up?

Scoring guide:
- 1.0: Build succeeds, structure matches architecture perfectly, all deps installed, clean config
- 0.8-0.9: Build succeeds with minor warnings, structure mostly matches, minor config issues
- 0.7: Build succeeds but some structural gaps or missing dependencies
- 0.5-0.6: Build fails with fixable errors, or significant structural mismatch
- Below 0.5: Build fails badly, major structural problems, missing critical dependencies

A score >= 0.7 passes. Below 0.7 fails and should be retried.

If the score is below 0.7, set recommendation to "retry_with_guidance" and provide specific instructions in the guidance field explaining exactly what needs to be fixed (include the build errors if any).

Respond with ONLY the following JSON object. Do not include any text before or after the JSON. Do not wrap it in markdown code fences.

{{"passed": true, "score": 0.85, "issues": ["list of specific problems found"], "recommendation": "proceed", "guidance": ""}}"""
