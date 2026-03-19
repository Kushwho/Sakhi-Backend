"""
Sakhi — LangGraph Chat Pipeline
==================================
A single-node ``StateGraph`` that:
  1. Reads the child's profile from LangGraph ``config``
  2. Prepends the personalised Sakhi system prompt
  3. Calls ``ChatGroq`` (via the centralized LLM layer)
  4. Returns the assistant reply (automatically checkpointed by LangGraph)

The graph is compiled once at startup with an ``AsyncPostgresSaver``
checkpointer so that each ``thread_id`` gets persistent conversation
memory in PostgreSQL.
"""

import logging
from typing import Any

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph

from services.checkpointer import get_checkpointer
from services.llm import get_chat_model
from services.prompts import build_system_prompt

logger = logging.getLogger("sakhi.chat_graph")

# ---------------------------------------------------------------------------
# Graph node
# ---------------------------------------------------------------------------


async def chat_node(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
    configurable = config.get("configurable", {})
    child_name = configurable.get("child_name", "buddy")
    child_age = configurable.get("child_age", 8)
    child_language = configurable.get("child_language", "English")
    mode = configurable.get("mode", "default")
    topic_context = configurable.get("topic_context")
    surprise_fact = configurable.get("surprise_fact")

    system_prompt = build_system_prompt(
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
        mode=mode,
        topic=topic_context,
        surprise_fact=surprise_fact,
    )

    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    model = get_chat_model()

    # Use ainvoke but bind the config so LangGraph can see the LLM events
    response = await model.ainvoke(messages, config)

    return {"messages": [response]}


# ---------------------------------------------------------------------------
# Graph builder (called once at startup)
# ---------------------------------------------------------------------------

_compiled_graph = None


def build_chat_graph():
    """Build and compile the LangGraph chat pipeline.

    Must be called after ``init_checkpointer()`` so the checkpointer is
    available.  The compiled graph is cached as a module-level singleton.
    """
    global _compiled_graph

    checkpointer = get_checkpointer()

    builder = StateGraph(MessagesState)
    builder.add_node("chat", chat_node)
    builder.add_edge(START, "chat")
    builder.add_edge("chat", END)

    _compiled_graph = builder.compile(checkpointer=checkpointer)
    logger.info("LangGraph chat pipeline compiled")
    return _compiled_graph


def get_chat_graph():
    """Return the compiled chat graph.  Raises if not built yet."""
    if _compiled_graph is None:
        raise RuntimeError("Chat graph not compiled — call build_chat_graph() first")
    return _compiled_graph
