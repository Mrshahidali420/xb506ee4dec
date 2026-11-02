# Finding the original title of a recap clip: research (2 Nov 2026)

Problem: a Facebook/TikTok recap clip, often English-dubbed with renamed characters, cropped, mirrored, captioned, and
watermarked. Find the real movie or drama (K, C, J, Thai, Indian, Hollywood). Ground truth used: refs/cry-ref1 is
"Bad Thief, Good Thief" (MBC 2017, K-drama); refs/intrigue-ref1 is the Chinese crime drama "The Truth" (风过留痕, 2026),
first unit case ("campus serial case", episodes 1-3, episode 2 turns on cigarette bite marks), found with Gemini search.

## What each option does (cost, limits, fit)

| Option | Cost / limits | Script-able | Asian dramas | Verdict |
|---|---|---|---|---|
| Post caption and description (yt-dlp info.json) | free | yes | n/a | USED. intrigue-ref1's caption said "Title/Credit - The truth". Most reliable signal when present. |
| Post comments via yt-dlp --write-comments | free | TikTok and YouTube yes; Facebook returned 0 comments (tested on our own clip) | n/a | USED for TikTok/YouTube only. Facebook needs a logged-in scrape; do not automate Facebook in the browser (account lock, Oct 2026). |
| trace.moe | free, 1000/mo no key | yes | anime only | USED (anime). |
| Gemini + Google Search grounding | free tier, flash models; grounding allowance reported as 500/day (others say 1500/day or 5000/month; docs conflict); overage $14-35 per 1000 grounded prompts | yes | good when it has text to search; blind frame matching of renamed dubs is weak | USED, core. |
| Google Lens via SerpApi (engine google_lens) | free 250/mo (some sources say 100), then $25 per 1000 | yes, but needs a PUBLIC image URL | strong for posters and known stills, weak for cropped/zoomed clip frames | OPTIONAL, not active (SERPAPI_KEY). |
| SearchAPI.io Google Lens / Yandex reverse image | paid, free trial; limits not confirmed | yes | Yandex good for faces and stills | OPTIONAL, not active. |
| Bing Visual Search API | retired 11 Aug 2025 | no | n/a | DEAD. |
| Yandex, TinEye, Baidu 识图, Sogou 识图 | no official free API; scraped via PicImageSearch (github.com/kituin/PicImageSearch), breaks behind captchas | fragile | Baidu/Sogou are the Chinese-drama ones | Not used. |
| AWS Rekognition RecognizeCelebrities | about $0.001 per image, 12-month free tier for new accounts (figures vary by source) | yes | coverage of Korean/Chinese actors not documented; faces in dubbed, mirrored clips may fail | OPTIONAL, not active (AWS keys). Needs card and AWS signup. |
| Azure celebrity recognition | Limited Access (Microsoft-managed customers only since 2022) | no | n/a | UNAVAILABLE to us. |
| Open-source face match (face_recognition + annoy, HF "actors_matching"; divya21raj/Actor-Recognition-In-Movies) | free | yes, but you build the actor image index (TMDb profile photos) | accuracy poor on East Asian faces; Hugging Face demo itself warns of mistakes | Rejected for now. |
| Open-source screenshot identifiers (ClipFind, SceneFind, kj3moraes/movieclip) | free | wrappers around Gemini + TMDb, or a CLIP experiment | no better than our cascade | Nothing to adopt. |
| OpenSubtitles API | free account, search is by title/hash, not by dialogue | yes | not useful: our transcript is a dub, not the original dialogue | Rejected. |
| TMDb API | free key | yes | good metadata and cast for Asian titles | USED as a check when TMDB_API_KEY is set. |
| MyDramaList unofficial API (mydramalist-client, mdlaw on PyPI) | free, undocumented app API behind Cloudflare, against site terms | fragile | best Asian-drama plot data | Not used; Gemini search reaches MDL pages. |
| Multimodal LLM with search from a plot summary (text only) | same as Gemini above | yes | worked for The Truth, partly for cry | USED as the plot-only pass. |

## What the two clips showed

- intrigue-ref1: caption "The truth" plus the plot pass reached 风过留痕 (2026). The strict verifier first rejected it because
  the dub renamed the detectives (Ryan, Daniel, Michael) and the synopsis lists 11 unit cases; fixed by telling the verifier
  that name differences are never a contradiction and to check each case of an anthology series. A targeted search then
  placed the case in the first unit case, episodes 1-3, episode 2 (ifensi.com, YouTube episode title).
- cry-ref1 (Bad Thief, Good Thief): the blind frame pass returned no title; the plot pass returned wrong candidates
  ("Pawn" 2020, "The Rural Heir Is a Genius" 2026) because it favoured recent titles and the names Su-Yin/Ethan/Danny are
  invented. When asked directly whether the clip is Bad Thief, Good Thief, Gemini confirmed it from the plot (boy with
  eidetic memory, hidden by a poor adoptive family, resentful elder son, jajangmyeon). Lesson: the search step works if the
  title is in the candidate list. So the plot prompt now asks for 5 candidates, any year, and every candidate is verified
  separately. What would have found it blind: a stronger model (gemini-3.1-pro-preview returned 429, no free quota on our key),
  a TikTok or YouTube comment, or a Korean-language plot query.
- The free Gemini key ran out of quota during testing (HTTP 429 on all models, 503 spikes on 2.5-flash). The tool now lists
  models from the API instead of hard-coding names (gemini-2.0-flash and 2.5-pro now return 404/no access), retries 503, and
  stops after a full failure so the verify steps do not hammer a dead quota.

## Chosen cascade (free first)

1. Caption, description and TikTok/YouTube comments (yt-dlp).
2. trace.moe for anime.
3. Gemini: 8 frames + caption + transcript, asked to read on-screen text and search; no face naming.
4. If below 0.6: plot-only pass, 5 candidates, any year.
5. Verify the top 5 with a separate search call; documentaries, news and real plot conflicts are demoted.
6. TMDb check (TMDB_API_KEY, free) for existence and cast.
7. Optional paid: SERPAPI_KEY (Google Lens, $25 per month for 1000 searches), AWS Rekognition for celebrity faces.
8. Always end with a human check of cast and plot before a title goes in a post.

## Sources

- SerpApi pricing roundups: https://apicostcalc.com/serpapi.html , https://www.octoparse.com/blog/free-serp-api
- SerpApi Google Lens docs: https://serpapi.com/blog/scrape-google-lens.md , https://github.com/serpapi/google-lens-scraper
- Bing Visual Search retirement: https://learn.microsoft.com/en-us/answers/questions/5538194/is-there-any-free-plan-for-bing-search-api , https://cloro.dev/blog/bing-search-api-key/
- Rekognition pricing roundups: https://wring.co/blog/aws-rekognition-pricing-guide , https://www.astuto.ai/blogs/amazon-rekognition-pricing-and-optimization
- Azure Limited Access: https://azure.microsoft.com/en-in/updates/facelimitedaccess/ , https://learn.microsoft.com/azure/azure-video-indexer/limited-access-features
- Gemini grounding quota discussion: https://discuss.ai.google.dev/t/grounding-free-quota-clarifying-2026-rules-for-gemini-2-5-flash-paid-tier-1/144593
- PicImageSearch (Baidu etc.): https://github.com/kituin/picimagesearch , https://pypi.org/project/picimagesearch
- Bright Data Google Lens (free 5K/month claim): https://brightdata.com/products/serp-api/google-search/lens
- Actor matching demo: https://huggingface.co/spaces/nbeuchat/actors_matching ; https://github.com/divya21raj/Actor-Recognition-In-Movies
- Open-source identifiers: https://github.com/FortuneNnah/ClipFind , https://github.com/N1tr029/SceneFind , https://github.com/kj3moraes/movieclip
- OpenSubtitles API notes: https://apidog.com/blog/opensubtitles-api , https://glama.ai/mcp/servers/@opensubtitles/mcp.opensubtitles.com
- MyDramaList unofficial clients: https://pypi.org/project/mydramalist-client/ , https://github.com/B1PL0B/MyDramaList-Unofficial-API
- yt-dlp Facebook issues (comments not covered): https://github.com/yt-dlp/yt-dlp/issues/4311 , https://github.com/yt-dlp/yt-dlp/issues/9720
- Episode lead for The Truth: ifensi.com page and a YouTube episode-2 title, as returned by Gemini search (not opened by hand)

Caveat: pricing figures come from third-party roundups and conflict between sources; confirm on the vendor page before paying.
