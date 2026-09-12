# The recorded UI demos

Three runs of the UI, recorded in Chromium at 1280x720 and 25fps, in a program
stream so they play anywhere -- two of them silent and the third with a
soundtrack, MP2 being the audio that stream carries.

| Video | The shopper asks for | Ends on | Sound |
| --- | --- | --- | --- |
| `wwii-books-1944-45.mpg` | *"wwii books about war in Europe 1944-45"* | the top 3, with the rest folded away | no |
| `wwii-books-1944-45-with-sound.mpg` | the same | the same | yes |
| `laptops-under-1000.mpg` | *"new laptop below 1000 USD, not too heavy or loud. windows 11 installed"* | the shop page behind the top product's link | no |

All three take the same shape: the request typed into the form, the progress
panel filling in as the pipeline works, and the top 3 of what survived grounding
landing on the page with the rest folded away underneath. The laptops one then
clicks through to what the shopper actually came for -- see *The link at the
end* below. Fourteen seconds, fourteen and twenty-two.

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

## Why MPEG-2 and not MPEG-1

The first two recordings are MPEG-1, and for this picture that was the wrong
format: 1280x720 is far outside MPEG-1's constrained parameters, so the encoder
declares a video buffer of 6 KB while its own keyframes run to 50, and every
pack the muxer writes violates the system target decoder. A player that ignores
all of that shows the film, which is why those two look fine. One that has to
schedule an audio track against the same model has no slack to ignore, and opens
nothing at all.

So the third take states its rate and its buffer rather than leaving them to
`-q:v`, and uses the codec whose levels this frame size is inside. It is still
one `.mpg` program stream and still plays anywhere -- more places, in fact, an
MPEG-2 program stream being the DVD lineage. `VIDEO` in `record.mjs` is the
whole of that decision, and the two silent recordings predate it: re-taking
either with `record.mjs` as it stands now writes MPEG-2 as well.

## The soundtrack

Chromium records no audio, so there is none in the run to capture and none of
this is a clip laid over it. `record.mjs` writes down a *cue* per thing that
happened -- every key of the request, the two clicks, each line as it arrives in
the progress panel, the results landing -- and `sound.py` synthesises those into
a WAV of exactly the video's length, which is then muxed in as MP2. Every sound
in it is a few sine waves under an envelope, so a recording taken again on
another machine comes out the same and nothing here is sampled or licensed from
anywhere.

The point of it is the fifth column of the table above. A log line that *took
something away* gets a note of its own -- lower, longer and unmistakable beside
the ordinary ticks -- so the six catches are audible without the panel being
read. `TOOK_SOMETHING_AWAY` in `record.mjs` is what decides which those are, and
it matches the verb rather than the count: a seventh heuristic says `Discarded`,
`Dropped` or `Merged` too, or it is not one that took anything away.

Most of a run's lines arrive in the same millisecond -- the model answers and
then five heuristics report at once -- and twenty notes struck together are one
loud chord that says nothing. `sound.spread` pushes them apart by `LINE_GAP`,
never earlier than the line they are about, so the same twenty read as the
flurry they are. For the same reason the mix is *limited* rather than
normalised: scaling the track by its loudest moment would let that one pile-up
decide how loud the typing was.

## What is not

The two slow, non-deterministic ends. `search_web` and `enrich` hand back the
script's own pages instead of reaching DuckDuckGo, the chat model is a script
rather than Ollama, and `GET /api/models` answers from a list rather than
asking. Neither Ollama nor the network is needed to reproduce either recording.

The book titles and their authors are real, and so are the laptop model names.
The shops, the prices, the ratings, the review counts, the weights and the
quoted verdicts are invented, and the hosts are all `*.example`, which cannot
resolve. Nothing on those pages is a claim about a real seller, a real reviewer
or a real price.

## The link at the end

`--follow-link` ends a recording the way a shopper ends a search: by clicking
the top product's name and reading the page it points at. It is worth watching
on the laptops run in particular, because the laptop that comes out first is the
one the fake model gave a link to a shop that was never searched -- so the page
that opens is the one `attribute_sources` put there instead, and the recording
ends on the difference between a grounded link and an invented one.

Those hosts cannot resolve, so nothing is fetched: `record.mjs` answers for
`*.example` itself, with the same page text `server.py` handed the pipeline,
laid out as the page it is pretending to be. `wwii-books-1944-45.mpg` was
recorded before the flag existed and stops at the results.

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

python -m demo.server --script books --pace 0.45 --port 8000     # and the third
node demo/record.mjs --url http://127.0.0.1:8000 --script books --sound `
    --out demo/wwii-books-1944-45-with-sound.mpg
```

`--sound` is the only difference between the last two but the pace, which is a
notch quicker there to keep the take inside fifteen seconds. The recorder prints
the length and the number of cues it collected, so a take that drifts out of
that says so without anybody opening it.

`--script` is passed to both, and has to name the same one twice: the server
searches that fabricated web, and the recorder reads the request to type and the
shop pages to answer out of it, so neither the sentence nor the pages are
written down a second time in JavaScript. `--request` types something else,
though each script's pages are written for its own.

`record.mjs` needs Playwright (locally installed or global -- it looks in both),
Python on PATH -- to read the script with, and to synthesise the track with --
and an ffmpeg with the `mpeg` muxer and the `mpeg2video` and `mp2` encoders. The
build Playwright ships beside its browsers has none of those -- it is stripped
down to WebM and VP8, which is what recording needs -- so a system ffmpeg is
preferred; `--ffmpeg` names a third.

## The README's picture

`docs/ui.png` -- the search form with its settings open, above the videos in the
main README -- is taken from this same server, by `screenshot.mjs` beside the
recorder:

```powershell
python -m demo.server --pace 0 --port 8000        # in one terminal
node demo/screenshot.mjs --out docs/ui.png        # in the other
```

`--pace 0` because nothing is waited on: the picture is of the form before
anyone presses the button, so the script never starts a run and the fabricated
web behind it is never searched. What it does need from `demo.server` is the
model dropdown and the header pill, both of which are answers from an Ollama --
taken against a plain `buy_agent.server` with none running, the picture shows
"Ollama unreachable" over a text box, which is what the old one showed for as
long as it went untaken.

It is clipped to the form card rather than to the viewport, so a field added to
the settings makes the picture taller instead of falling off the bottom of it,
and rendered at twice the CSS width, since GitHub scales a README image down to
its column. `--url`, `--width`, `--scale` and `--request` move the rest.

Nothing here is imported by `buy_agent/` or by either test suite: `pytest.ini`
keeps `testpaths = tests`, so this directory is never collected, and
`.dockerignore` keeps it out of the image.
