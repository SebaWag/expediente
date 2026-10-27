"""The navigation layer.

An agent that answers questions by *walking a graph* — choosing the next edge
given what the previous hop found — rather than by retrieving a text span.

The traversal is deterministic and bounded, so every route the agent takes can be
replayed. That is the whole reason it is allowed to choose at all.
"""
