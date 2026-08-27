# The recorded UI demo

`wwii-books-1944-45.mpg` is one run of the UI, recorded in Chromium at 1280x720
and 25fps: the shopper types *"wwii books about war in Europe 1944-45"*, the
progress panel fills in as the pipeline works, and the top 3 of what survived
grounding land on the page, with the rest folded away underneath. Fifteen
seconds, no audio, MPEG-1 in a program stream so it plays anywhere.

## What is real in it

Everything between the search and the ranking. The pages go through the real
`fetch.condense`, and what the fake model claims it read off them is then put
through the real `clean_products`, `ground`, `deduplicate` and `rank_products`
-- so every line in the progress panel is a line this project writes, every
figure on a card is one grounding accepted, and the scores are the ones
`ranking.py` computed. `demo/pages.py` makes the fake model wrong in the six
ways a small model is wrong, and the panel shows each of them being caught:

| Log line | What was wrong |
| --- | --- |
| `Discarded 1 result(s) that were pages, not products` | A listicle headline reported as a book |
| `Dropped 1 product(s) absent from the search results` | A title no page mentions |
| `Dropped unsupported figures on 1 product(s)` | A price no page printed -- the card reads "price unknown" |
| `Dropped 1 opinion(s) the sources never printed` | A verdict nobody wrote |
| `Dropped 1 link(s) to pages that were never searched` | A link to a page the agent never saw |
| `Merged 1 duplicate listing(s)` | One book listed twice, in two currencies |

## What is not

The two slow, non-deterministic ends. `search_web` and `enrich` hand back
`demo/pages.py` instead of reaching DuckDuckGo, the chat model is a script
rather than Ollama, and `GET /api/models` answers from a list rather than
asking. Neither Ollama nor the network is needed to reproduce the recording.

The book titles and authors are real. The shops, the prices, the ratings, the
review counts and the quoted verdicts are invented, and the hosts are all
`*.example`, which cannot resolve. Nothing on those pages is a claim about a
real seller, a real reviewer or a real price.

## Recording it again

`--pace` is what keeps the waiting out of the recording. A real run spends most
of a minute inside two model calls that log nothing, which is dead air on tape;
the scripted stand-ins take the same shape with two orders of magnitude off the
clock, and `--pace` scales what is left. At 0.6 no step is silent for longer
than about a second, which is what the recorded take used.

```powershell
cd ui ; npm install ; npm run build ; cd ..     # server.DEFAULT_UI_DIR wants this
python -m demo.server --pace 0.6 --port 8000    # in one terminal
node demo/record.mjs --url http://127.0.0.1:8000 --out demo/wwii-books-1944-45.mpg
```

`record.mjs` needs Playwright (locally installed or global -- it looks in both)
and an ffmpeg with the `mpeg` muxer and the `mpeg1video` encoder. The build
Playwright ships beside its browsers has neither, so a system ffmpeg is
preferred; `--ffmpeg` names a third. `--request` changes what gets typed, though
`demo/pages.py` is written for this one.

Nothing here is imported by `buy_agent/` or by either test suite: `pytest.ini`
keeps `testpaths = tests`, so this directory is never collected, and
`.dockerignore` keeps it out of the image.
