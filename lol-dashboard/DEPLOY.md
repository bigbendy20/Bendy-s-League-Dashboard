# Deploying the board

One-time setup, in the order that actually works. Roughly an hour of your
attention plus a few hours of unattended backfill.

Everything below is free: Streamlit Community Cloud, a Postgres free tier,
GitHub Actions on a public repo, and Auth0 up to 25,000 monthly users.

---

## Before you start

Three things that are easy to get wrong and annoying to undo.

**The repo must be public.** GitHub Actions is unlimited on public repos and
capped at 2,000 minutes/month on private ones — and Actions bills per job
rounded up to a whole minute, so the 5-minute refresh cron would cost about
8,600 minutes. Public repo, private data: the site is gated by sign-in, and
nothing sensitive is committed.

**Three files must never be committed.** All three are already in
`.gitignore`; check before your first push.

| File | Holds |
|---|---|
| `.env` | Your Riot API key |
| `roster.txt` | Your friends' Riot IDs |
| `.streamlit/secrets.toml` | OAuth credentials, the invite list |

**Your friends' consent is a prerequisite, not a formality.** You're about to
put their match history in a database and show each other their tilt
patterns. Their data is public via any tracker, but that isn't the same as
you building a site about it.

---

## Order of operations

The slow step doesn't need any of the accounts, so do it first.

```
   Backfill locally  ──────────────►  hours, unattended, needs nothing but
   (Backfill.bat)                     your API key. Start this tonight.
          │
          ▼
   Postgres + Auth0  ──────────────►  ~20 minutes of signups
          │
          ▼
   Upload the database  ───────────►  seconds (upload_store.py)
          │
          ▼
   Deploy + turn on the refresher
```

---

## 0. Backfill first (start now, walk away)

Double-click **`Backfill.bat`**, or:

```bash
python seed_profiles.py     # resolve the 8 Riot IDs — seconds
python refresh_job.py --backfill
```

This writes `data/board.db`, a single SQLite file. Nothing is uploaded
anywhere; that's a separate step you can do days later.

**It's resumable.** Stop it, reboot, run it again — it never re-fetches what
it already has. Your own history is already cached, so only your friends
cost API calls.

**One caution:** the project folder is inside OneDrive, and sync clients can
corrupt an open SQLite file. Pause OneDrive while it runs, or point it
elsewhere:

```bash
set DATABASE_URL=sqlite:///C:/temp/board.db
```

The scripts print a warning if they notice they're writing into a synced
folder.

---

## 1. Postgres (5 minutes)

Create a free database at [Neon](https://neon.tech) or
[Supabase](https://supabase.com). Either is fine; both give ~500 MB free,
and eight players at 1,000 games each uses about 31 MB.

Copy the connection string. It looks like:

```
postgresql://user:password@host/dbname
```

Keep it somewhere for the next two steps. It is a credential — treat it like
the API key.

---

## 2. Auth0 (15 minutes)

Sign up at [auth0.com](https://auth0.com), then:

1. **Applications → Create Application** → *Regular Web Application*
2. Under **Settings**, add to *Allowed Callback URLs* (comma-separated):
   ```
   http://localhost:8501/oauth2callback,
   https://<your-app>.streamlit.app/oauth2callback
   ```
   You won't know the second URL until step 4 — come back and add it.
3. Under **Authentication → Social**, enable whichever your group uses:
   Google, Microsoft, Apple, GitHub. Leave *Database* enabled too, so anyone
   without a social login can use email and password.
4. Note your **Domain**, **Client ID** and **Client Secret**.

The reason for Auth0 rather than wiring Google directly: your group is on
mixed email hosts, and this is one client that covers all of them plus
email/password. If you'd rather wire providers individually, the alternative
config is commented in `.streamlit/secrets.example.toml` — the app renders one
button per configured provider either way.

---

## 3. Local secrets, and uploading what you backfilled

```bash
cd lol-dashboard
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
```

Edit `secrets.toml`:

- `allowed_emails` — the email addresses your friends will sign in with.
  Not their Riot IDs. Get these from them; a guess locks someone out.
- `cookie_secret` — generate one:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- `[auth.auth0]` — the domain, client ID and secret from step 2.

Then push the database you built in step 0 into Postgres:

```bash
# See what would be copied, without writing.
python upload_store.py --to "postgresql://..." --dry-run

# Do it. Seconds.
python upload_store.py --to "postgresql://..."
```

**Safe to re-run.** Nothing is duplicated, and nothing in the destination is
deleted — so if the refresher has already collected newer games, the upload
adds to them rather than replacing them.

Sanity check:

```bash
python -c "
import refresh_job
s = refresh_job.open_store('postgresql://...')
for p in s.game_counts(): print(p)
"
```

---

## 4. Deploy (10 minutes)

Push to a **public** GitHub repo, then at
[share.streamlit.io](https://share.streamlit.io):

1. **New app** → pick the repo → main file `lol-dashboard/app.py`
2. **Advanced settings → Secrets** → paste the entire contents of your local
   `secrets.toml`, and add the database URL:
   ```toml
   DATABASE_URL = "postgresql://..."
   ```
3. Deploy. Note the URL.
4. Go back to Auth0 and add `https://<that-url>/oauth2callback` to the
   allowed callbacks. **Sign-in fails with a confusing error until you do.**
5. In `secrets.toml` on Streamlit, set `redirect_uri` to the deployed URL.

---

## 5. Turn on the refresher

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**, twice:

| Name | Value |
|---|---|
| `RIOT_API_KEY` | Your key |
| `DATABASE_URL` | The Postgres connection string |

The workflow (`.github/workflows/refresh.yml`) runs every 5 minutes on its
own. Trigger it once by hand from the **Actions** tab to confirm it works —
the log prints what it found.

---

## Checks worth doing

- [ ] Open the site signed out — you should see a sign-in button, no data.
- [ ] Sign in with an address **not** on the list — you should be refused,
      and the message should name the address you used.
- [ ] Sign in properly — the site should open on *your* profile.
- [ ] Switch to a friend's profile; the URL should change and be shareable.
- [ ] Check the Actions tab after ten minutes — two runs, both green.
- [ ] `git log --stat | grep -E "\.env|roster\.txt|secrets\.toml"` returns
      nothing.

---

## When something breaks

**"Not on the invite list" with your own address.** The address in
`allowed_emails` doesn't match the one your provider returned. The error
message names the address it actually saw — copy that one in.

**Sign-in redirects and then errors.** The callback URL isn't registered in
Auth0, or `redirect_uri` in secrets doesn't exactly match the deployed URL.
Both must be the full absolute URL ending in `/oauth2callback`.

**The site loads but every profile is empty.** `DATABASE_URL` isn't set in
Streamlit's secrets, so the app is reading the local file store — which
doesn't exist on the server. It will look like a data problem and isn't.

**Actions runs succeed but nothing updates.** Check the log for
`fetch failed`; that's an API key problem. `no puuid` means a profile was
never seeded — re-run `seed_profiles.py`.

**The app sleeps.** Community Cloud hibernates apps after 12 hours without
visitors. The next visit wakes it, taking a few seconds. The refresher keeps
running regardless, since it doesn't live in the app.
