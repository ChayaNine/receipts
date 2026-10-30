# Deploying the Receipts demo

> **October 2026 update:** Creating a Hugging Face CPU Basic Docker Space requires a paid subscription. For a free hosting route, see [DEPLOY_RENDER.md](DEPLOY_RENDER.md).

The hackathon needs a public demo URL that keeps working until judging ends (December 15, 2026).
Hugging Face Spaces is free, runs the included Dockerfile as-is, and only sleeps after about 48 hours without visitors.

## Before you deploy: record the samples

Recorded runs make the three sample buttons replay instantly on the hosted demo, even if your credits run out.

```bash
python scripts/record_samples.py
```

This writes three files to `data/cache/`. Commit them to your GitHub repo. Read the verdicts it prints; if one looks wrong, that's useful material for the limitations section, or re-run with `--force`.

## Option A: Hugging Face Spaces (recommended, free)

1. Sign in at https://huggingface.co and click **New Space**.
2. Name it `receipts`, choose **Docker** as the SDK and **Blank** as the template, hardware **CPU basic (free)**, visibility **Public**. Create it.
3. In the Space, open **Files → Add file → Upload files** and upload everything in this project **except** `.env` (include `data/cache/*.json`).
4. Replace the Space's `README.md` with the contents of `docs/huggingface-space-README.md` (it holds the settings Spaces needs). Keep the project README on GitHub.
5. Open **Settings → Variables and secrets** and add two **secrets**: `NEBIUS_API_KEY` and `TAVILY_API_KEY`. Add the `MODEL_*` variables too if `scripts/doctor.py` told you to change them.
6. The Space builds (2–4 minutes) and shows the app. Your demo URL is `https://<your-username>-receipts.hf.space`.
7. Test it in a private browser window: click each sample (they should say "Replay of a live run"), then paste a paragraph of your own to confirm live checks work.

Abuse protection is built in: each visitor gets 12 live checks per hour and the server stops live checks after 300 per day (`LIVE_RUNS_PER_HOUR`, `LIVE_RUNS_PER_DAY`). Recorded sample replays are unlimited.

## Option B: Nebius Serverless Endpoints (optional)

Running the app itself on Nebius is encouraged by the track but not required, and it needs a Nebius AI Cloud account with billing enabled (Token Factory credits may not cover it).

1. Build and push the image to a public registry, for example Docker Hub:
   ```bash
   docker build -t <dockerhub-user>/receipts:1 .
   docker push <dockerhub-user>/receipts:1
   ```
2. Create a CPU endpoint, following the Nebius Serverless quickstart (https://docs.nebius.com/serverless/quickstart/endpoints):
   ```bash
   nebius ai endpoint create \
     --name receipts \
     --image <dockerhub-user>/receipts:1 \
     --platform cpu-d3 --preset 4vcpu-16gb \
     --container-port 7860 \
     --subnet-id <subnet_ID>
   ```
   Pass `NEBIUS_API_KEY` and `TAVILY_API_KEY` as environment variables using the options described in the Nebius docs for your CLI version.
3. Get the HTTPS URL from the **Public endpoints** field in the console.

Check that the URL opens **without** an auth token in a private window before you submit it: judges must be able to open it freely. The Nebius quickstart describes token authentication, so confirm the access setting for your endpoint. If you deploy here, mention "Nebius Serverless Endpoints" in your Devpost "Built with" list and README.

## Option C: any Docker host

```bash
docker build -t receipts .
docker run -p 7860:7860 -e NEBIUS_API_KEY=... -e TAVILY_API_KEY=... receipts
```
