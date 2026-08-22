"""A LangChain + Ollama agent that searches the web for products and ranks them."""

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
