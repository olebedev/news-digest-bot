# News Digest Bot

Automated digest bot that publishes Atom feeds with paging/archives for GitHub Pages consumption. The repo name `news-digest-bot` keeps it flexible for adding more sources later. Each source keeps its code, state, and outputs under `sources/<slug>/` to avoid path mix-ups.

## Running locally

- `python run.py` to generate all sources and place publishable XML under `public/`.

## Feeds

- HN digest feed:
  - `https://<owner>.github.io/<repo>/hn/feed.xml`
  - If you use a custom domain, replace with `https://your-domain/hn/feed.xml`
  - Archives are linked from `feed.xml` as `feed-1.xml`, etc.

## Deployment

- GitHub Pages workflow (`.github/workflows/pages.yml`) runs daily (cron) and on demand, generates feeds via `run.py`, and publishes the `public` directory (XML only) to Pages. Requires the `OPENAI_API_KEY` secret. Optionally set `FEED_BASE_URL` for a custom domain.
