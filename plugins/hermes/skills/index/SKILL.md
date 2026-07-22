---
description: Build or manage indexes for compiled books. Use when the user wants to create a summary index, chunk index, or graph index for a book, or check index status.
allowed-tools:
  - mcp__bookscout__compile_book
  - mcp__bookscout__get_compile_progress
  - mcp__bookscout__get_book
  - mcp__bookscout__list_books
---

# Manage Indexes

When the user wants to build indexes for a book:

1. Check which indexes already exist by calling `get_book` or `list_books`.
2. Available index types and their requirements:
   - **Summary index**: Requires LLM configuration. Generates chapter-level summaries. Best for conceptual search and quick overviews. Lightweight.
   - **Chunk index**: Requires embedding configuration. Splits content into semantic chunks with vector embeddings. Best for precise passage retrieval. Medium weight.
   - **Graph index**: Requires both LLM and embedding. Builds a knowledge graph of entities and relationships. Best for "how does X connect to Y" queries. Heaviest.
3. To build an index, call `compile_book` — the index build is triggered as part of the compilation pipeline with the appropriate flags.
4. Monitor progress with `get_compile_progress`. Index building can take minutes for large books.
5. Report completion and suggest trying `/bookscout:search` to test the new index.

## Index Selection Guide

| User wants to…                            | Best index      |
| ----------------------------------------- | --------------- |
| Get quick overviews / "what's this about" | Summary         |
| Find exact passages / quotes              | Chunk           |
| Explore relationships / connections       | Graph           |
| Do all of the above                       | Build all three |

## Examples

- "Build a summary index for the design patterns book"
- "Create all indexes for the ML book"
- "Which books don't have chunk indexes yet?"
