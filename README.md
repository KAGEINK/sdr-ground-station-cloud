# SDR Weather Satellite Ground Station — Cloud Integration

Cloud layer for the graduation project **"Design and Implementation of a Low-Cost SDR
Ground Station for Weather Satellite Image Reception"** (Jazan University, CNET).

Images decoded by SatDump on the station PC are uploaded to Supabase and shown on a
public Streamlit dashboard that anyone can open from a browser.

```
QFH antenna -> LNA + filter -> RTL-SDR -> SatDump (station PC)
                                             |
                                 uploader/upload_passes.py
                                             |
                          Supabase (Postgres tables + Storage bucket)
                                             |
                        Streamlit dashboard (dashboard/app.py, public URL)
```

| Folder | What it is | Where it runs |
|---|---|---|
| `supabase/schema.sql` | Tables, read-only security policies, storage bucket | Supabase SQL Editor (once) |
| `uploader/` | Sends new SatDump passes to the cloud | Station PC |
| `dashboard/` | Web dashboard | Streamlit Community Cloud |
| `.github/workflows/keep-alive.yml` | Stops the free database from pausing | GitHub Actions |

## Setup

### 1. Supabase
1. Create a project at supabase.com.
2. SQL Editor -> paste `supabase/schema.sql` -> Run.
3. Project Settings -> API: copy the **Project URL**, the **public key**
   (anon / publishable) and the **secret key** (service_role / secret).

### 2. Station PC (uploader)
```bash
pip install -r uploader/requirements.txt
copy .env.example .env        # then edit .env
python uploader/upload_passes.py --dry-run   # check what it finds
python uploader/upload_passes.py             # upload
python uploader/upload_passes.py --watch     # leave running during reception days
```

### 3. Dashboard
Local test:
```bash
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # then edit
streamlit run dashboard/app.py
```
Deploy: share.streamlit.io -> New app -> this repository -> main file
`dashboard/app.py` -> Advanced settings -> Secrets: paste the two lines from
`secrets.toml`.

### 4. Keep-alive (optional)
Repository Settings -> Secrets and variables -> Actions: add `SUPABASE_URL` and
`SUPABASE_KEY` (public key).

## Security
- The dashboard only holds the public key and the database policies allow it to **read only**.
- The secret key lives only in `.env` on the station PC. `.env` and `secrets.toml` are in
  `.gitignore` and must never be committed.

## Team
Khaled Abdullah Amr · Ali Mohammed Qohal · Abdulrhman Ali Otayf · Yousef Ibrahim Alzarawlh ·
Ahmed Adel — Supervisor: Dr. Hussein Zangoti
