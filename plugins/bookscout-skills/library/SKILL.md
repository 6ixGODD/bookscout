---
description: List and browse books in the BookScout library. Use when the user wants to see what books they have, check which indexes are built, or get book metadata.
allowed-tools:
  - mcp__bookscout__list_books
  - mcp__bookscout__get_book
---

# Browse Library

When the user wants to see their book collection:

1. Call `list_books` to get all books with their metadata.
2. Present as a formatted list:

   ```
   📚 BookScout Library (3 books)

   1. Design Patterns — Gang of Four
      Indexes: summary ✓  chunk ✓  graph ✗

   2. Machine Learning — Tom Mitchell
      Indexes: summary ✓  chunk ✗  graph ✗

   3. Clean Code — Robert Martin
      Indexes: summary ✗  chunk ✗  graph ✗
   ```

3. If the user asks about a specific book, call `get_book` with the book_id for detailed information (author, description, node count, index status).
4. If a book has no indexes, suggest `/bookscout:index` to build them.

## Examples

- "Show me my books"
- "What books do I have in my library?"
- "Tell me about the machine learning book"
