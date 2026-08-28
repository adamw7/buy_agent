"""A LangChain agent that searches the web for products and ranks them.

The model is a local one, served by Ollama or by vLLM -- ``AgentConfig.provider``
chooses which (ADR-0003, ADR-0028).
"""

from buy_agent.agent import BuyAgent
from buy_agent.config import AgentConfig
from buy_agent.models import Product, RankedProduct
from buy_agent.ranking import RankingWeights, rank_products

__all__ = [
    "AgentConfig",
    "BuyAgent",
    "Product",
    "RankedProduct",
    "RankingWeights",
    "rank_products",
]
