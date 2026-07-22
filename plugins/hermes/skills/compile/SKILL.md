---
description: Compile a book from PDF or EPUB into BookScout's structured format. Use when the user wants to add a new book, import a document, or build a book's ontology.
allowed-tools:
  - mcp__bookscout__compile_book
  - mcp__bookscout__get_compile_progress
---

# Compile a Book

When the user wants to add a book to their library:

1. Ask for the file path if not provided (PDF or EPUB).
2. Call `compile_book` with the path and builder type:
   - `"rule"` (default) — fast, deterministic, no LLM cost. Good for most books.
   - `"llm"` — uses an LLM for higher-quality parsing. Slower, costs tokens. Use only when the user explicitly asks for it or the rule builder fails.
3. Monitor progress with `get_compile_progress` using the returned task ID. Poll every few seconds until the task completes.
4. Report the result — book title, number of nodes (chapters/sections), and which indexes are available.
5. Suggest building indexes if none are present (see `/bookscout:index`).

## Examples

- "Add this PDF to my library: D:/books/machine-learning.pdf"
- "Import the EPUB at ~/Downloads/design-patterns.epub"
- "Compile /path/to/book.pdf using the LLM builder for better quality"
