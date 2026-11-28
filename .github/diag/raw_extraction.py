"""Throwaway diagnostic: print what qwen3:0.6b extracts before Python judges it."""

import logging
import sys

from benchmark.cases import HEADPHONES
from benchmark.runner import serving_the_corpus
from buy_agent.agent import BuyAgent

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, format="%(name)s %(levelname)s %(message)s")
for noisy in ("httpx", "httpcore", "openai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

config = HEADPHONES.settings(model="qwen3:0.6b")
with serving_the_corpus(HEADPHONES.pages, HEADPHONES.page_text):
    agent = BuyAgent(config)
    chain = agent.extraction_chain
    seen = []

    class Recording:
        def invoke(self, payload):
            answer = chain.invoke(payload)
            seen.append(answer)
            return answer

    agent.extraction_chain = Recording()
    ranked = agent.run(HEADPHONES.request)
    agent.close()

print("=== RAW EXTRACTION ===")
print(seen[-1].model_dump_json(indent=1) if seen else "none")
print("=== RANKED ===")
for entry in ranked:
    p = entry.product
    print(p.name, p.price, p.currency, p.rating, p.review_count)
