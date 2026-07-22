---
description: Read specific sections or chapters of a compiled book. Use when the user wants to read part of a book, navigate its structure, or get the content of a specific node.
allowed-tools:
  - mcp__bookscout__get_book_nodes
  - mcp__bookscout__get_node_content
---

# Read a Book

When the user wants to read from a compiled book:

1. Call `get_book_nodes` with the book_id to get the table of contents (chapters, sections, subsections).
2. Present the structure as a navigable outline:

   ```
   📖 Design Patterns — Table of Contents

   1. Introduction
   2. Case Study: Designing a Document Editor
   3. Creational Patterns
      3.1 Abstract Factory
      3.2 Builder
      3.3 Factory Method
   4. Structural Patterns
      ...
   ```

3. When the user picks a section, call `get_node_content` with the node_id to retrieve the full text.
4. Format the content nicely — preserve headings, paragraphs, and structure. Truncate very long sections and offer to continue.
5. After reading, suggest:
   - Continue to the next section
   - Jump to another section by number or name
   - Search for specific content with `/bookscout:search`

## Examples

- "Read chapter 3 of the design patterns book"
- "Show me the table of contents for Clean Code"
- "What does the ML book say in the section about neural networks?"
