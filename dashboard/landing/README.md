# Landing page

Marketing landing page shown at `/` before the main dashboard at `/app`. React +
Vite + Tailwind v4.

A self-contained standalone app that builds with plain `npm` (no workspace, no
`pnpm-workspace.yaml` catalog).

## Structure

- `src/` — React + Vite + Tailwind source
  - `src/lib/utils.ts` — the shadcn `cn()` helper (imported as `@/lib/utils`)
  - `attached_assets/` — build-time image imports (`@assets/…`), e.g. the logo
- `public/` — static passthrough assets (favicon, robots.txt)
- Built static output is copied into `../frontend/` for deployment (see below).

## Build (standalone)

```bash
cd dashboard/landing
npm install            # plain npm; uses the committed package-lock.json
npm run build          # → dist/public/  (BASE_PATH defaults to /, PORT to 5173)
npm run typecheck      # tsc --noEmit (optional; currently clean)
npm run dev            # local dev server
```

`PORT` / `BASE_PATH` are optional (they default); set them to override, e.g.
`BASE_PATH=/some/sub/path npm run build`.

## ⚠️ Refreshing the shipped `../frontend/` bundle keeps a small auth layer

The production landing page (`dashboard/frontend/index.html`, served at `/`) is the
Vite `index.html` **plus a small inline auth layer** that can't live in the static
React bundle. That layer is, by design, all that remains hand-written in
`index.html`:

- an **auth-gate `<script>`** in `<head>` that redirects already-logged-in visitors
  straight to `/app` (runs before React to avoid a content flash);
- the **`#landingAuthModal`** markup + its `<style id="landing-auth-patch">` and
  end-of-body `<script>` — a signup/sign-in modal that talks to `/api/auth/*` and
  a click delegation that funnels the landing's CTAs into it.

Everything else the landing shows — the nav (with its native `data-landing-auth`
**Start Free** and **Sign in** buttons), the Discord-prompt and paper-trading sections, the
fixed-height agent playground — is now rendered **natively by the React source**.
(An earlier shipped bundle injected those via a `MutationObserver` patch; the source
has since absorbed them, and that obsolete patch machinery was removed so it can't
duplicate sections or fight the native grid nav.)

So a bundle refresh is now close to mechanical:

```bash
npm run build
cp dist/public/assets/* ../frontend/assets/     # new content-hashed JS/CSS/img
# remove the superseded ../frontend/assets/index-*.{js,css} + atl-logo-*.png
# In ../frontend/index.html: point the <script>/<link> at the NEW asset
#   filenames. KEEP the auth-gate <script>, #landingAuthModal markup,
#   <style id="landing-auth-patch">, and the end-of-body auth <script>.
```

The asset filenames are content-hashed, so **no `?v=` cache-buster is needed** —
a new build produces a new name, which is the cache bust. (Older revisions of this
file told you to bump one; the shipped `index.html` has carried none since the
`frontend/` refresh, and adding one now would just be noise.)

`dashboard/backend/tests/test_frontend_bundle_integrity.py` guards the mechanical
half of the steps above in CI: every `/assets/...` reference in `index.html` must
resolve, no superseded bundle may be left behind, and the four hand-written auth
markers must still be present. It does **not** rebuild the bundle — Vite's content
hashes move with toolchain versions, so a reproducibility check would be flaky.

The auth layer binds to native `data-landing-auth` buttons + CTA labels, so it no
longer depends on any patch-injected DOM. Still: it touches the live signup/login
flow, so verify in a browser (or headlessly — load `/`, confirm each section renders
once, the modal opens on every **Start Free** button *in signup mode* and on the
navbar's **Sign in** button *in login mode*, and the only console error is
the `/_vercel/insights/script.js` 404, which exists off Vercel and is expected) before
shipping. Note the **Sign in** control is `lg:`-gated — widen the viewport past
1024px or it legitimately will not be in the DOM's layout. Longer term, folding the auth modal + gate into the React source would
remove even this remnant, making the build output *exactly* the shipped page.

