# Deploy Receipts to Render (free web service)

This is a faster alternative to the original Hugging Face Docker Space instructions. As of October 30, 2026, Hugging Face requires a paid subscription for new CPU Basic Docker Spaces, whereas Render currently offers a free Python web-service plan.

## 1. First run locally on Windows

In PowerShell inside the extracted `receipts` folder:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe scripts\doctor.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Set `NEBIUS_API_KEY` and `TAVILY_API_KEY` in `.env` first. Open `http://localhost:8000` and test all three samples and your own citation paragraph. If any model ID is wrong, use the IDs listed by `doctor.py` in `MODEL_FAST`, `MODEL_JUDGE`, or `MODEL_REASON`.

## 2. Record genuine examples BEFORE publishing

After your first *genuine* live runs, stop Uvicorn with Ctrl+C and run:

```powershell
.\.venv\Scripts\python.exe scripts\record_samples.py
```

Confirm you have three `.json` files in `data/cache` with a successful `done` event. These can be replayed without API usage. Replace `docs/screenshot.png` with a screenshot of a genuine live check (the original file is simulated). Never present mock output as genuine.

## 3. Push this project's contents to a public GitHub repo

Create an empty public repo at `https://github.com/new` named `receipts` (do not initialize with a README). Then in PowerShell from inside the `receipts` folder:

```powershell
git init
git add .
git commit -m "Deploy Receipts citation checker"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/receipts.git
git push -u origin main
```

Replace `YOUR_USERNAME`. Before you push, verify `.env` is excluded:

```powershell
git status --short
git ls-files .env
```

The second command must print nothing. Include `render.yaml`, `.python-version`, and the recorded `data/cache/*.json` files in the repo.

## 4. Deploy from GitHub using Render Blueprint

1. Open `https://dashboard.render.com/` and sign in.
2. Click **New > Blueprint** and connect your public `receipts` GitHub repo.
3. Choose the `main` branch and leave Blueprint Path as `render.yaml`.
4. Review the **Free** web service named `receipts`.
5. Fill in the **secret** prompts for `NEBIUS_API_KEY` and `TAVILY_API_KEY` in the Render UI. **Never put their values into `render.yaml` or your repository.**
6. Click **Deploy Blueprint**. In the created web service, watch the Events/Logs until it says live.
7. Open the assigned `https://<your-app>.onrender.com/` URL. The actual app name may have a suffix if `receipts` is taken.

Render uses Python 3.12 here rather than its current Python 3.14 default. The Blueprint runs Uvicorn with Render's `$PORT` environment variable. Your bundled Dockerfile is retained for alternative hosts but is not used by this Render Blueprint.

## 5. Validate the public application

- `https://<your-app>.onrender.com/api/config` should return JSON with `mode: live` and the sample list; it must not say `simulated`.
- If the samples were recorded and uploaded, they should say **Replay of a live run**.
- Paste a new sourced paragraph and confirm a genuine Nebius/Tavily analysis completes end-to-end.
- Open the URL in a private browser window to verify it works without logging in.
- Take and upload genuine screenshots. Put the public GitHub and Render links into Devpost.

## Important caveats

- Render Free spins down after 15 minutes without inbound traffic; it can take roughly a minute to restart.
- Render Free's filesystem is ephemeral. Therefore commit your recorded sample JSON files to the repo. New runtime cache files will disappear on instance restart/redeploy.
- Tokens/credits for Nebius and Tavily are separate from free application hosting. The default limit is 12 live checks per visitor per hour and 300 total per day, *not a guaranteed spending ceiling*.
- The project has been simulated and locally tested; actual Nebius and Tavily live calls are still necessary.
- Hackathon rules and eligibility, including the legal-age requirement, are on Devpost.

Official docs: https://render.com/docs/infrastructure-as-code ; https://render.com/docs/free ; https://render.com/docs/python-version
