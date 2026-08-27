"""Scripted runs of the pipeline, for recording the UI without Ollama or the web.

:mod:`demo.books` and :mod:`demo.laptops` are the two fabricated webs a run can
search, and ``demo.server --script`` picks between them.

Everything between the search and the ranking is the *real* code: the fabricated
pages go through :func:`buy_agent.fetch.condense`, and what the fake model says
it read is then put through the real ``clean_products``, ``ground``,
``deduplicate`` and ``rank_products``. Only the two slow, non-deterministic ends
are stubbed -- DuckDuckGo and the LLM -- so the progress log in the recording is
the log this project actually writes, and the figures on the cards are figures
grounding accepted.

The pages, the prices and the ratings are invented, on ``*.example`` hosts that
cannot resolve. The book titles, their authors and the laptop model names are
real; nothing else on those pages is, and none of it is a claim about a real
shop.
"""
