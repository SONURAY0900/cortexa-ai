from typing import Literal

from pydantic import BaseModel, Field
from langchain_groq import ChatGroq

from config.settings import GROQ_API_KEY
from prompts.router_prompt import ROUTER_PROMPT
from utils.logger import log_agent


class RouteDecision(BaseModel):

    route: Literal[
        "DIRECT",
        "WIKIPEDIA",
        "TAVILY",
        "ARXIV"
    ] = Field(
        description="The route selected for the user's query."
    )

    reason: str = Field(
        description="Short explanation for why this route was selected."
    )


router_llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
    api_key=GROQ_API_KEY
)


structured_router = router_llm.with_structured_output(
    RouteDecision
)


def route_query(query: str) -> RouteDecision:

    log_agent(
        "ROUTER",
        f"Analyzing query: {query}"
    )

    prompt = ROUTER_PROMPT.format(
        query=query
    )

    decision = structured_router.invoke(
        prompt
    )

    log_agent(
        "ROUTER",
        f"Route selected: {decision.route}"
    )

    log_agent(
        "ROUTER",
        f"Reason: {decision.reason}"
    )

    return decision