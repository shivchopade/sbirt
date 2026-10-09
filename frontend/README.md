# Frontend — Resume Screening Engine

Deliberately minimal Next.js (App Router) + TypeScript + Tailwind UI. See the root `README.md` for the full project.

```bash
npm install
npm run gen:api     # regenerate src/lib/api-types.ts from ../contracts/openapi.json
npm run dev         # http://localhost:3000 (expects the API on http://localhost:8000)
npm run typecheck && npm run lint
```

Set `NEXT_PUBLIC_API_URL` to point at a different API. Screens: `/` (jobs + new job), `/jobs/[id]` (requirements,
skills, upload, candidate table), `/candidates/[id]` (report, flags with evidence, previews, decision).
