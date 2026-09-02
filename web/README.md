# Fantasy Scout Control Centre

The component-based replacement for the legacy static dashboard.

## Local development

```bash
npm install
npm run dev
```

The server-side data adapter uses the versioned FastAPI service through `FPL_API_BASE_URL`. It retains `FPL_DATA_BASE_URL` as a temporary migration fallback. Copy `.env.example` to `.env.local` for local integration.

## Validation

```bash
npm run typecheck
npm run build
```

## Architecture

- Next.js App Router and TypeScript
- Server Components with a hybrid live-team and snapshot-backed data model
- Automatic current-gameweek discovery through the official FPL bootstrap cache
- Server-side official FPL live polling through `/v1/live/team`, with a hosted-snapshot fallback
- Reusable application shell, pitch, player and metric components
- Typed Competitive V4 API adapter in `lib/competitive.ts`; the browser does not duplicate the scoring formula
- Production dashboard is deployed from `master`; live and snapshot states are labelled in the UI

See [`docs/CURRENT_STATUS.md`](../docs/CURRENT_STATUS.md) for the authoritative production,
Gameweek and deployment status.

The production cutover steps and the Telegram safety boundary are documented in the repository's `DEPLOYMENT.md`.
