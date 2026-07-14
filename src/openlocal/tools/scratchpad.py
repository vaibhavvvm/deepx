"""Scratchpad tool for the agent to write down temporary thoughts."""

from langchain_core.tools import tool

@tool
def scratchpad(notes: str) -> str:
    """Write temporary thoughts, plans, or reasoning to a scratchpad.
    
    Use this tool instead of outputting long reasoning in your chat response.
    This saves context window and keeps the conversation clean.
    
    Args:
        notes: Your internal reasoning, planning steps, or things to remember.
        
    Returns:
        A confirmation that the notes were recorded.
    """
    # We don't actually need to persist this on disk unless we want to.
    # Just returning success means the model's notes are now in the 
    # LangChain message history (as a tool call argument) for it to reference later!
    return "Notes recorded to scratchpad."
