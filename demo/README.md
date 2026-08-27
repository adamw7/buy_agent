# The recorded UI demos

Two runs of the UI, recorded in Chromium at 1280x720 and 25fps, no audio,
MPEG-1 in a program stream so they play anywhere.

| Video | The shopper asks for | Ends on |
| --- | --- | --- |
| `wwii-books-1944-45.mpg` | *"wwii books about war in Europe 1944-45"* | the top 3, with the rest folded away |
| `laptops-under-1000.mpg` | *"new laptop below 1000 USD, not too heavy or loud. windows 11 installed"* | the shop page behind the top product's link |

Both take the same shape: the request typed into the form, the progress panel
filling in as the pipeline works, and the top 3 of what survived grounding
landing on the page with the rest folded away underneath. The laptops one then
clicks through to what the shopper actually came for -- see *The link at the
end* below. Fifteen seconds and twenty-two.

Each has a script of its own -- `books.py` and `laptops.py` -- holding the ten
pages that demo searches and the answer the fake model gives when it is asked to
read products off them. `--script` picks between them, and a third demo is a
module beside those two offering the same five names, plus a row in
`server.SCRIPTS`.

## What is real in it

Everything between the search and the ranking. The pages go through the real
`fetch.condense`, and what the fake model claims it read off them is then put
through the real `clean_products`, `ground`, `deduplicate` and `rank_products`
-- so every line in the progress panel is a line this project writes, every
figure on a card is one grounding accepted, and the scores are the ones
`ranking.py` computed. Both scripts make the fake model wrong in the same six
ways a small model is wrong, and the panel shows each of them being caught:

| Log line | What was wrong |
| --- | --- |
| `Discarded 1 result(s) that were pages, not products` | A listicle headline reported as a product |
| `Dropped 1 product(s) absent from the search results` | A name no page mentions |
| `Dropped unsupported figures on 1 product(s)` | A price no page printed -- the card reads "price unknown" |
| `Dropped 1 opinion(s) the sources never printed` | A verdict nobody wrote |
| `Dropped 1 link(s) to pages that were never searched` | A link to a page the agent never saw |
| `Merged 1 duplicate listing(s)` | One product listed twice, in two currencies |

## What is not

The two slow, non-deterministic ends. `search_web` and `enrich` hand back the
script's own pages instead of reaching DuckDuckGo, the chat model is a script
rather than Ollama, and `GET /api/models` answers from a list rather than
asking. Neither Ollama nor the network is needed to reproduce either recording.

The book titles and their authors are real, and so are the laptop model names.
The shops, the prices, the ratings, the review counts, the weights and the quoted
verdicts are invented, and the hosts are all `*.example`, which cannot resolve.
Nothing on those pages is a claim about a real seller, a real reviewer or a real
price.

## The link at the end

`--follow-link` ends a recording the way a shopper ends a search: by clicking
the top product's name and reading the page it points at. It is worth watching
on the laptops run in particular, because the laptop that comes out first is the
one the fake model gave a link to a shop that was never searched -- so the page
that opens is the one `attribute_sources` put there instead, and the recording
ends on the difference between a grounded link and an invented one.

Those hosts cannot resolve, so nothing is fetched: `record.mjs` answers for
`*.example` itself, with the same page text `server.py` handed the pipeline, laid
out as the page it is pretending to be. `wwii-books-1944-45.mpg` was recorded
before the flag existed and stops at the results.

## Recording them again

`--pace` is what keeps the waiting out of a recording. A real run spends most of
a minute inside two model calls that log nothing, which is dead air on tape; the
scripted stand-ins take the same shape with two orders of magnitude off the
clock, and `--pace` scales what is left. At 0.6 no step is silent for longer
than about a second, which is what both takes used.

```powershell
cd ui ; npm install ; npm run build ; cd ..     # server.DEFAULT_UI_DIR wants this

python -m demo.server --script laptops --pace 0.6 --port 8000    # in one terminal
node demo/record.mjs --url http://127.0.0.1:8000 --script laptops --follow-link `
    --out demo/laptops-under-1000.mpg

python -m demo.server --script books --pace 0.6 --port 8000      # the other one
node demo/record.mjs --url http://127.0.0.1:8000 --script books `
    --out demo/wwii-books-1944-45.mpg
```

`--script` is passed to both, and has to name the same one twice: the server
searches that fabricated web, and the recorder reads the request to type and the
shop pages to answer out of it, so neither the sentence nor the pages are
written down a second time in JavaScript. `--request` types something else,
though each script's pages are written for its own.

`record.mjs` needs Playwright (locally installed or global -- it looks in both),
Python on PATH to read the script with, and an ffmpeg with the `mpeg` muxer and
the `mpeg1video` encoder. The build Playwright ships beside its browsers has
neither of the last two, so a system ffmpeg is preferred; `--ffmpeg` names a
third.

Nothing here is imported by `buy_agent/` or by either test suite: `pytest.ini`
keeps `testpaths = tests`, so this directory is never collected, and
`.dockerignore` keeps it out of the image.
