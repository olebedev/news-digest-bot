# Hacker News source

HN-specific settings and algorithm details for the digest bot.

## How it works

- Fetch the top 500 HN stories (`topstories` API).
- Track scores across runs in `sources/hn/state.json`. When a story’s score **crosses** the threshold (`THRESH=100`) and it hasn’t been seen before, it becomes a candidate.
- Candidates are sorted by score and only the first `MAX_ITEMS=8` are processed each run.
- For each candidate:
  - Fetch and summarize the main URL (one short factual paragraph).
  - Fetch the HN thread HTML page and summarize it, focusing on the most visible/upvoted comment themes and overall discussion.
  - Record the entry in the Atom feed history (bounded to `MAX_HISTORY_ENTRIES=200`).
- Publish Atom feed pages of size `PAGE_SIZE=200` (`feed.xml`, `feed-1.xml`, …) with RFC 5005-style archive links. Stale feed pages are cleaned up automatically.

## Why some 100+ point stories may not appear

- The story must **cross** the threshold in the current run; already-hot stories above 100 before a run won’t reappear.
- Only the top `MAX_ITEMS` crossing stories per run are included.
- Only `topstories` (first 500) are scanned; anything outside that list is ignored.
- Items already present in the feed history are permanently deduped.

## Outputs

- Atom feed with paging in `sources/hn/out/feed.xml` (and `feed-1.xml`, etc.). The top-level runner copies only these XML files into `public/hn/` for Pages.
- `FEED_BASE_URL` (env) sets absolute self/prev/next links for Pages/custom domains; the runner appends `/hn` automatically.
- Source-specific isolation: HN lives entirely under `sources/hn/` (code, state, outputs).
