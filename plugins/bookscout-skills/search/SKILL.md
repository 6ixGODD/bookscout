---
description: Search across compiled books for relevant content. Use when the user asks about book topics, wants to find passages, or needs information from their library.
allowed-tools:
  - mcp__bookscout__search_summaries
  - mcp__bookscout__search_chunks
  - mcp__bookscout__search_graph
---

# Search Books

When the user wants to find information in their book library:

1. Determine the best search type based on the question:
   - **Summary search** (`search_summaries`): Best for conceptual questions, overviews, "what does the book say about X", "explain the main idea of". This is the most versatile — start here if unsure.
   - **Chunk search** (`search_chunks`): Best for finding specific passages, quotes, exact text, "find where it says". Returns the most granular results.
   - **Graph search** (`search_graph`): Best for relationship queries, "how does X connect to Y", "what influences Z". Explores the knowledge graph.
2. Call the appropriate search function with `book_id` and `query`. Use `top_k=10` by default; increase to 20 for broad topics.
3. Present results clearly:
   - Book title and chapter/section name
   - Relevance score (if available)
   - Key excerpt (truncated to ~200 chars)
4. If results are insufficient:
   - Try a different search type (e.g., chunks if summaries were too broad)
   - Refine the query with more specific terms
   - Suggest the user try `/bookscout:read` to explore a specific section

## Examples

- "What does the machine learning book say about gradient descent?"
- "Find passages about design patterns in my software engineering book"
- "How does reinforcement learning connect to neural networks?"
