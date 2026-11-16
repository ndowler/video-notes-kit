---
name: video-notes
description: "Use when given a YouTube link for notes or search."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [youtube, transcripts, notes, search, treg]
    related_skills: [treg]
---

# Video notes

Turn a YouTube link into a timestamped Markdown transcript plus a short set of takeaways, stored in a searchable repo managed by the `vn` CLI from https://github.com/ndowler/video-notes-kit.

## Prerequisites

- `vn` on PATH (clone the repo, then `ln -s "$PWD/vn" ~/.local/bin/vn`). Set `VN_ROOT` to use a notes repo somewhere else.
- The `treg` CLI, logged in with a funded account (https://treg.to).

## Steps

1. `vn add "<youtube url>" --tags a,b` fetches captions via treg (`crawl4ai.youtube.video.captions`, about $0.0017 per video) and writes `videos/<date>-<slug>/transcript.md` and `meta.json`. It prints the folder.
2. Read the transcript. Write takeaways as Markdown: numbered `##` sections with timestamps in parentheses, a header noting the caption type and any garbled names, and a closing `## Applying it to your own work` section tied to the user's real projects. See `examples/takeaways-template.md` in the repo. Mark sponsor reads and pitches as such.
3. `vn attach <video-id> takeaways <file>`, then commit in the notes repo if it is under git.
4. Reply with a short list of the most useful takeaways and the file paths. Run `vn search` on the core idea and point out links to other captured videos.

## Other commands

`vn list`, `vn search "<q>" [--in transcripts|takeaways|all] [-n N] [--json]` (FTS5, BM25, deep `&t=` links), `vn reindex`, `vn import <transcript.md>`.

## Pitfalls

- Bulk channel captures (a whole channel, hundreds of videos) cost real treg money and time. Estimate the count and cost and get a yes from the user before starting.
- Auto-captions mangle names and product terms. Trust the video title over the captions and note the likely errors in the takeaways header.
- `vn add` refuses a duplicate video ID. Use `vn list` or `vn search` first if unsure.
- `vn add` only handles YouTube. For Instagram reels, get the speech transcript with treg `scrapecreators.x.v2-instagram-media-transcript --query url=<reel>` (the recipe sources field gives the caption only). Import by hand: write `videos/<date>-<slug>/transcript.md` in the same `**[m:ss](url?t=N)**` paragraph format plus a `meta.json` with video_id `ig-<shortcode>`, then run `vn reindex`.
- Short social clips are often lead magnets with inflated claims. Fact-check any cited research with a web search and say what it actually found.
- Never commit or publish transcripts of other people's videos to a public repo without checking you have the right to.
