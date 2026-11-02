# REF-STUDY: how to study a reference clip

Shahid shares a Facebook/YouTube/TikTok clip from another recap page. Each one must teach the writer something new. Learn what is new; do not pile up duplicates.

## Steps

1. Run the tool (from the repo root):
   `uv run -q --python 3.11 --with yt-dlp --with shazamio --with pillow --with requests --with truststore python tools/study_ref.py <url> [name]`
   It fills `refs/<name>/` (git-ignored): `video.mp4` (480p), `audio.mp3`, `sheets/sheet_NN.jpg` (a frame every 3 s, time on each tile), `transcript.txt`, `music.txt`, `meta.json`. Needs `GROQ_API_KEY` in the env or `~/.config/watch/.env`. Never print it.
2. Look at every frame sheet. Note who is on screen, set, on-screen text, watermark, caption style.
3. Read `transcript.txt`, `music.txt`, `meta.json` (words per minute, cuts per 10 s).
4. Identify the original movie/drama/anime: on-screen names, faces, names in the transcript, web search. Write "unsure" if unsure. Never guess a title.
5. Study the FULL original, not a plot summary (standard step). Once it is identified, make a prep job on GitHub like our own jobs; never download the film to the PC:
   - `jobs/ref-<name>/source.json` = `{"type":"kisskh","search":"<title>","episode":<n>,"shot_sheets":true}` for a film or K-drama (anime: `{"type":"anime","title":"<title>","episode":<n>,"lang":"dub","shot_sheets":true}`; formats in `tools/daily.py` `movie_job` / `series_job`). For a drama series fetch only the episodes the clip covers.
   - Commit and push it, then `gh workflow run build.yml -f job=jobs/ref-<name>`, `gh run watch <id> --exit-status`, `gh run download <id> -n sheets -D jobs/ref-<name>/sheets`. This gives `film_sheet_*.jpg` (a frame every 30 s with time stamps), shot sheets and `english.txt` (subtitles with seconds). Do not commit sheets or videos.
   - Compare scene by scene: map each line of the reference video to the time in the original (minute:second); list what they used, skipped, reordered and invented; how much original screen time each part of their video covers; where the video starts (hook) against the real story order; how 2 h became N minutes. Put this map in `refs/<name>/analysis.md`.
   - Fallback only if the full source cannot be found: compare with a plot summary (Wikipedia, AsianWiki, MyDramaList, AniList) and SAY SO in analysis.md. If even the title is unknown, write "unsure" and analyse the clip alone.
6. Emotion mechanics: why it makes people cry or laugh. Name the setup and its payoff, music swells, silence, the line just before the cut, what the viewer knows that the character does not.
7. Music: which track plays where, and why that mood there.
8. Script style: hook, sentence length, tense, names, bridges, judgment lines, ending, caption look.
9. Write `refs/<name>/analysis.md` (keep paraphrased; at most 2 short quoted lines).
10. Update `REF-LESSONS.md`: for each finding, look for an existing line first. If it repeats, bump its count and add the ref name; only add a new line for something genuinely new. At 3 sightings mark it STRONG and add it to "Proposed WRITER.md changes" (propose only; Shahid approves).

## Rules

- Never paste a full transcript into a tracked file; quote at most 2 short lines per video.
- Raw files stay in `refs/`. Only `tools/study_ref.py`, `REF-STUDY.md`, `REF-LESSONS.md` are tracked.
- A finding with no evidence (time stamp, frame, line) does not go in the lessons.
- Report to Shahid in a few lines: original title (or unsure), strongest findings, music, new lessons added.
