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
from langgraph.graph import StateGraph, MessagesState, START, END

from services.llm import get_chat_model
from services.checkpointer import get_checkpointer

logger = logging.getLogger("sakhi.chat_graph")

# ---------------------------------------------------------------------------
# System prompt template (same as agent.py / former chat_routes.py)
# ---------------------------------------------------------------------------

SAKHI_SYSTEM_PROMPT = """\
You are Sakhi, a warm, curious, and encouraging AI companion for Indian children aged 4–12.

Personality:
- You are playful, patient, and full of wonder.
- You celebrate small wins enthusiastically.
- You speak in short, simple sentences appropriate for children.
- You are strictly child-safe — no violence, no inappropriate content, ever.

Teaching style:
- You NEVER give direct homework answers.
- You use Socratic questioning — ask guiding questions to help children discover answers themselves.
- When explaining concepts, use fun analogies, stories, and examples from everyday Indian life.

Conversation style:
- Keep responses concise (2-3 sentences max for young children, up to 4-5 for older ones).
- Use a cheerful, expressive tone.
- If the child seems confused, simplify and try a different approach.
- If the child seems sad or upset, be empathetic and supportive.

You are currently talking to {child_name}, who is {child_age} years old and prefers {child_language}.
Adjust your vocabulary and complexity to match their age.\
"""

# ---------------------------------------------------------------------------
# Graph node
# ---------------------------------------------------------------------------


async def chat_node(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
    configurable = config.get("configurable", {})
    child_name = configurable.get("child_name", "buddy")
    child_age = configurable.get("child_age", 8)
    child_language = configurable.get("child_language", "English")

    system_prompt = SAKHI_SYSTEM_PROMPT.format(
        child_name=child_name,
        child_age=child_age,
        child_language=child_language,
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
        raise RuntimeError(
            "Chat graph not compiled — call build_chat_graph() first"
        )
    return _compiled_graph
