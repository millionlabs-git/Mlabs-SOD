# Product Requirements Document

## Overview

Replace this document with your product requirements. The SOD system will read this PRD and automatically build the application described here.

## Features

- Describe the features you want built
- Include user stories, acceptance criteria, and UI expectations
- Be specific about data models, API behavior, and business logic

## Tech Stack

This template uses:
- **Frontend:** React 19 + Vite + Tailwind CSS + wouter (routing)
- **Backend:** Express 4 + express-session (auth)
- **Database:** PostgreSQL + Drizzle ORM
- **Deployment:** Replit (production) / Fly.io (development)

## Pages

List the pages/routes your app needs:

1. `/` — Home page
2. `/login` — Login page
3. `/dashboard` — Main dashboard (authenticated)

## Data Models

Describe your data models beyond the base `users` table:

- Users (provided by template)
- Add your domain models here

## API Endpoints

List the API endpoints beyond the base auth routes:

- `GET /api/health` — Health check (provided)
- `POST /api/auth/login` — Login (provided)
- `POST /api/auth/register` — Register (provided)
- `POST /api/auth/logout` — Logout (provided)
- Add your API endpoints here
