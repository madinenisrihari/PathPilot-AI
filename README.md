# PathPilot AI

PathPilot AI is an all-in-one career companion: it generates personalized 12-week learning
roadmaps, helps you prep for interviews and build an ATS resume, tracks goals/streaks/badges,
and gives you a lightweight AI mentor chat — all in one mobile-friendly Streamlit app.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Optional: enable real AI (Gemini) for Mentor Chat & Mock Interview

Without a key, those two features still work using built-in fallback logic (curated tips /
static question banks). To get fully personalized AI responses instead:

1. Get a free API key at https://aistudio.google.com/app/apikey
2. `pip install google-generativeai` (kept out of requirements.txt on purpose — see below)
3. Create `.streamlit/secrets.toml` in the project folder:
   ```toml
   GEMINI_API_KEY = "your-key-here"
   ```
4. Restart the app — a sidebar note will confirm AI mode is active.

`google-generativeai` is intentionally **not** in requirements.txt because it's a heavy package
that slows down first-time deploys/cold-starts. The app checks for it with a safe `try/except`
import, so it runs fine without it — add it yourself only if you're using the AI chat/interview.

## If the app looks stuck loading / shows blank gray boxes

That's Streamlit's loading "skeleton" placeholder, not a crash. It usually means either:
- Your connection is slow (the app has to download the page + fonts + first script run)
- The app is "waking up" from Streamlit Community Cloud's sleep mode after inactivity (first
  load after sleeping can take 30–60 seconds)

Give it a few seconds, then pull-to-refresh. If it's still blank after ~a minute, check the
"Manage app" logs on Streamlit Cloud for an actual error.

On Streamlit Community Cloud, add `GEMINI_API_KEY` under your app's **Settings → Secrets** instead
of committing a secrets file.

## What's real vs. demo in this version

Everything in the app is interactive and functional for a single session. A few features are
intentionally simplified because they'd need paid third-party services or a real backend to be
production-grade — each is labeled in the UI:

| Feature | Current state | To make it production-grade |
|---|---|---|
| Login/Signup | **Real accounts** — SQLite database, salted + hashed passwords (PBKDF2, no plaintext ever stored). Works across sessions/devices while the app stays running. On free Streamlit Community Cloud hosting the database file resets on a redeploy/reboot (ephemeral disk) — a "Continue as guest" option is still available for quick tries | Swap SQLite for a hosted DB (Supabase/Firebase/Postgres) so accounts survive redeploys |
| Jobs & Internships | Static sample listings | Connect a job-board API |
| LeetCode tracker | Manual entry | LeetCode has no official public API; would need an unofficial GraphQL integration |
| GitHub integration | **Live** — pulls real public profile data via the GitHub REST API | Already real |
| Admin/Analytics dashboard | Shows only your current session | Needs a database to aggregate across real users |
| Premium mode | A toggle switch | Needs real payments (Stripe/Razorpay) + entitlement checks |
| AI Mentor Chat / Mock Interview | Rule-based fallback, or real Gemini if a key is set | Already real once a key is added |

All your data (roadmap, resume, goals, badges, etc.) is stored in the browser session, so it
resets when the app restarts or the session ends — that's a Streamlit limitation without a
connected database.
