# video-notes-kit

A small toolkit for turning YouTube videos into searchable Markdown notes. It fetches the captions, writes a timestamped transcript with deep links, lets you attach your own takeaways, and builds a local full-text search over all of it. It also includes a skill for the Hermes agent so an agent can run the same workflow from a chat message.

The tool is a single Python 3 program using only the standard library.

## How it works

1. `vn add <url>` calls the treg tool `crawl4ai.youtube.video.captions`. This costs about $0.0017 per video.
2. The captions are grouped into paragraphs of roughly 60 to 90 seconds and written to `transcript.md`. Each paragraph starts with a timestamp that links to that moment in the video (`...&t=392s`).
3. You (or your agent) read the transcript and write a takeaways file, then add it with `vn attach`. A template is in `examples/takeaways-template.md`.
4. `vn search` queries a SQLite FTS5 index built from the Markdown. Results show the video, timestamp, deep link and a snippet.

Everything is plain files in a folder, so it works well with git.

## Requirements

- Python 3.8 or newer with SQLite FTS5 (standard in most builds; there is a slower LIKE fallback).
- The `treg` CLI and an account, needed only for `vn add`. Sign up at https://treg.to, install the CLI as described there, and log in with `treg login` (check `treg --help` if your version differs). Fund the account; captures are paid per call.
- `vn list`, `vn search`, `vn reindex`, `vn attach` and `vn import` work without treg.

## Install

```
git clone https://github.com/ndowler/video-notes-kit.git
cd video-notes-kit
mkdir -p ~/.local/bin
ln -s "$PWD/vn" ~/.local/bin/vn
```

Make sure `~/.local/bin` is on your PATH. `vn` follows its own symlink back to the clone and keeps notes in `videos/` there. To keep notes elsewhere, set `VN_ROOT` to that directory (it needs a `videos/` folder, which `vn` creates when it adds one).

`VN_TREG` sets the path of the treg binary if it is not on your PATH.

## Usage

```
vn add <youtube-url> [--takeaways FILE] [--tags a,b]
vn attach <video-id-or-slug> takeaways FILE
vn search "<query>" [--in transcripts|takeaways|all] [-n 10] [--json]
vn list
vn reindex
vn import transcript.md [--takeaways FILE] [--channel NAME] [--tags a,b]
```

- `add` fetches captions with treg, writes `videos/<date>-<slug>/transcript.md` and `meta.json`, updates the index and prints the folder. It refuses a duplicate video ID and exits 1 if captions are missing or treg fails. Accepts watch, youtu.be, shorts, embed and live URLs, or a bare 11-character ID.
- `attach` adds or replaces `takeaways.md` for a video. The slug may be a unique substring.
- `search` ranks with BM25 and porter stemming, so "skill" matches "skills". All terms must match; if nothing does, it falls back to any term. Transcript hits are per paragraph. Takeaway hits are per heading section. `--json` returns a list of objects with `video_id, slug, title, kind, heading, timestamp, start_s, url, file, snippet, score`.
- `list` shows date, title, length and whether takeaways exist.
- `reindex` rebuilds the search database and `INDEX.md` from the Markdown. The index also rebuilds on its own when it is missing or older than any file under `videos/`, for example after a `git pull`.
- `import` loads a transcript file that is already in the `vn` format (see below), which is how you add sources `vn add` does not handle.

### Transcript format

```
# Video title

- Source: https://www.youtube.com/watch?v=VIDEOID
- Length: about 12:30
- Transcript: auto-generated English captions, unedited

## Transcript

**[0:00](https://www.youtube.com/watch?v=VIDEOID&t=0s)** First paragraph text.
```

## Using it with Hermes

Install the skill:

```
hermes skills install https://raw.githubusercontent.com/ndowler/video-notes-kit/main/skills/video-notes/SKILL.md
```

Then send your agent a YouTube link and ask for notes. The skill tells it to run `vn add`, read the transcript, write takeaways in the template format, attach them and summarize. It needs `vn` and `treg` available on the machine where the agent runs.

## Using it without Hermes

Run the commands yourself, or paste a transcript into any assistant and ask for takeaways in the format of `examples/takeaways-template.md`, then save the result and run `vn attach`. `skills/video-notes/SKILL.md` is plain Markdown and can be adapted as instructions for another agent.

## Cost warning

Each `vn add` is a paid treg call. One video is cheap, but capturing a whole channel means hundreds of calls and takes real time. Work out the count and the cost before you start a bulk run.

Auto-generated captions often misspell names and product terms. Check them against the video title before relying on them.

Transcripts of other people's videos are their content. Keep your notes repo private unless you have the right to publish them.

## Repo layout

```
vn                           command, resolves its own symlink
vnlib.py                     implementation
tests/test_vn.py             unit tests (treg is mocked, no network)
examples/takeaways-template.md
skills/video-notes/SKILL.md  Hermes skill
videos/                      your notes, one folder per video:
  <date>-<slug>/transcript.md
  <date>-<slug>/takeaways.md   optional
  <date>-<slug>/meta.json      video_id, title, channel, url, length, captured, caption_type, tags
INDEX.md                     generated list of videos, do not edit
.index/vn.sqlite             search index, gitignored
```

## Tests

```
python3 -m unittest discover -s tests
```

## License

MIT. See `LICENSE`.
