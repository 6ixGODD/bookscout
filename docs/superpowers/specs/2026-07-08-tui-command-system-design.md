# TUI Command System & index_select Redesign — Design

**Date:** 2026-07-08
**Status:** Approved → Implementation Plan

## Problem

The current TUI has three groups of usability problems:

1. **`index_select` phase renders a plain Static list** of `[x] c Chunk` /
   `[ ] g Graph  (slow, expensive)` lines. Users toggle by *typing a letter*
   in the input box. There is no real widget, no description of what each
   index does, and the affordance is invisible to anyone who has used a
   modern checkbox UI.

2. **Commands and free-form paths are mixed in the same input box.** In the
   select phase the user can type `1`, `mybook.pdf`, `:delete 2` and the
   handler branches on prefix / type / extension. In chat phase a path with
   `.pdf` / `.epub` suffix also triggers compile. There is no single rule;
   the behavior surprises users. The `>` "prompt" was implemented as an
   Input placeholder, which disappears as soon as the user types — it is
   not the BIOS-style fixed prompt the user wants.

3. **There is no symmetry between "select an existing book" and "compile a
   new book".** Both feel like throwaway inputs instead of dedicated
   commands.

## Design Goals

- Every non-chat input **must** start with `:`. Non-`:` input outside chat
  is rejected with a short error. (Natural-language parsing of bare input
  is explicitly future work, not in scope.)
- `>` is a **fixed, non-deletable prompt prefix** rendered to the left of
  the input — not a placeholder, not part of the Input's value.
- `index_select` uses real **Textual `Checkbox` widgets**, one per
  `IndexProvider`, each with a description line and toggleable by mouse or
  keyboard (Tab + Space/Enter).
- `:book N` enters the chat for an existing book (single-select only).
- `:compile <path>` adds a new book (parse + index build). The free-form
  path typing in the select phase is removed.
- Chat phase keeps its existing commands (`:quit`, `:back`, `:clear`,
  `:addindex X`, `:rmindex X`). Path-suffix autodetect is removed from chat;
  adding a book always goes through select + `:compile <path>`.
- No changes to `ReplContext`, `ReadingMode`, `ReadingAgentToolset`, or
  backend packages. The redesign is confined to the TUI layer plus one new
  `description: str` field on `IndexProvider` (backward-compatible default).

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  IndexProvider  (bookscout-doccompiler)                              │
│    + description: str = ""    ← NEW (backward-compatible)            │
│  Three INDEX_PROVIDER singletons each set a descriptive sentence.   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  BookScoutTui (bookscout-repl)                                       │
│                                                                      │
│  compose():                                                          │
│    #header  Static "BookScout" / Static hint / Rule                  │
│    #main    (phase-switched panel container)                          │
│       #select_panel        ListView #books_list                      │
│                             Static  #error_display                   │
│       #index_select_panel  Static  #index_select_hint                 │
│                             Checkbox #cb_chunk                        │
│                             Checkbox #cb_summary                      │
│                             Checkbox #cb_graph   (N, registry size)  │
│                             Static  #index_select_error               │
│       #compile_panel       RichLog #compile_log                      │
│                             Static  #spinner_line                    │
│       #chat_panel          RichLog #chat_log                         │
│                             Static  #chat_spinner_line               │
│    #input_area  Horizontal                                            │
│       Static "> " (id="prompt_prefix", white bold, fixed width 2)    │
│       Input     (id="select_input", no placeholder)                  │
│       Input     (id="chat_input",  no placeholder)                   │
│    Static #status_bar                                                 │
│                                                                      │
│  on_input_submitted: dispatch by phase                                │
│    select      → _handle_select_command(value)                       │
│    index_select → _handle_index_select_command(value)                │
│    chat        → _handle_chat_command(value)                          │
│                                                                      │
│  @on(Checkbox.Changed) → sync self._selected_index_types             │
└─────────────────────────────────────────────────────────────────────┘
```

## §1 — `IndexProvider.description`

Add a new field at the end of the dataclass (keeps `slots=True` ordering
backward-compatible because every instantiation uses keyword args):

```python
# bookscout/doccompiler/index_provider.py
@dataclasses.dataclass(frozen=True, slots=True)
class IndexProvider:
    index_type: str
    display_name: str
    short_letter: str
    requires_vector_store: bool
    default_enabled: bool
    indexer_factory: IndexerFactory
    tool_factory: ToolFactory
    store_factory: StoreFactory
    db_path_name: str
    description: str = ""        # ← NEW
```

Three provider singletons populate `description`:

| index_type  | description |
|---|---|
| chunk    | "Passage-level chunks for precise citation and semantic search" |
| summary  | "Book-level digest; cheap, good for high-level questions" |
| graph    | "Relationship map between entities; slow and expensive" |

## §2 — Fixed `>` prompt

`#input_area` becomes a `Horizontal` container holding:

```python
with Horizontal(id="input_area"):
    yield Static("> ", id="prompt_prefix")
    yield Input(id="select_input")     # no placeholder
    yield Input(id="chat_input")       # no placeholder
```

CSS rules:

```css
#input_area {
    height: 3;
    layout: horizontal;
    padding: 0 0 0 0;
}
#prompt_prefix {
    width: 2;
    height: 3;
    color: #ffffff;
    text-style: bold;
    padding: 0 0 0 1;
    background: #000000;
}
#select_input, #chat_input {
    width: 1fr;
    border-top: solid #ffffff;
    border-bottom: solid #ffffff;
    border-left: none;
    border-right: none;
    background: #000000;
    color: #c0c0c0;
    padding: 0 0 0 0;
    height: 3;
}
Input:focus { /* unchanged */ }
```

- The two `Input`s retain the existing show/hide-by-phase logic in
  `_set_panel`; `#prompt_prefix` is always visible.
- `Input` no longer has a `placeholder` argument — the BIOS prompt visual is
  rendered entirely by `#prompt_prefix` to its left.
- The prompt prefix is a separate Static widget, so users cannot delete it
  by backspacing in the Input. The Input's `.value` only ever contains the
  user-typed command.

## §3 — `index_select` phase: real Checkboxes

### Composition

The `#index_select_panel` Container composes:

```python
with Container(id="index_select_panel"):
    yield Static("Indexes to build:", id="index_select_hint")
    # One Checkbox per provider, dynamically created at run time.
    yield Checkbox("", id="cb_chunk")    # label set in _render_index_select
    yield Checkbox("", id="cb_summary")
    yield Checkbox("", id="cb_graph")
    yield Static("", id="index_select_error")
```

Because the registry might grow, the ids follow the convention
`cb_<index_type>`. The compose could also iterate the registry in a
`yield from`, but Textual `compose()` returning a generator over a
non-hardcoded list is fine; for v1 we hardcode the three ids and break / add
new ones if providers change.

### Rendering labels with descriptions

`_render_index_select()` builds each Checkbox label as rich text:

```python
from rich.text import Text
provider = registry.by_type("chunk")
label = Text.assemble(
    Text(provider.display_name, style="bold"),
    Text("  "),
    Text(provider.description, style="dim"),
)
cb = self.query_one("#cb_chunk", Checkbox)
cb.label = label           # Textual accepts a Rich renderable as label
cb.value = provider.index_type in self._selected_index_types
```

If `provider.description` is empty, the dim trailing span is omitted.

### Default state

`_enter_index_select(source_path)` initializes
`self._selected_index_types = {p.index_type for p in registry.default_enabled()}`
then calls `_render_index_select()` which sets each checkbox's `.value`
accordingly. (Chunk + Summary = on, Graph = off by current registry.)

### Toggle interaction

- **Mouse**: clicking a Checkbox toggles it (Textual default).
- **Keyboard**: Tab moves focus into the checkbox group; Space / Enter
  toggles the focused checkbox (Textual default).
- **Notification handler**:

```python
@on(Checkbox.Changed)
def _checkbox_changed(self, event: Checkbox.Changed) -> None:
    cb_id = event.checkbox.id or ""
    if not cb_id.startswith("cb_"):
        return
    index_type = cb_id[3:]
    if event.value:
        self._selected_index_types.add(index_type)
    else:
        self._selected_index_types.discard(index_type)
    self._set_status(f"  indexes: {', '.join(sorted(self._selected_index_types)) or 'none'}")
```

This real-time sync means `_selected_index_types` is always authoritative;
pressing Enter in the phase's input simply reads the current set.

### Confirm and cancel

The index_select phase still **uses the input box for commands**. Tab moves
the focus between the input and the checkboxes; the input retains primary
focus after each command via `_focus_input()`.

| Input | Effect |
|---|---|
| Enter empty / `:go` | if `_selected_index_types` non-empty → `start_compile(source_path, index_types=_selected_index_types)`; else status "select at least one index" |
| `:back` | discard, `phase = "select"` |
| `:quit` | exit app |
| anything else not starting with `:` | error "Unknown command (commands start with `:`)" |
| unknown `:foo` | error "Unknown command: :foo" |

The previous "type a letter to toggle" behavior is removed; all toggling
happens through the Checkbox widgets themselves.

## §4 — Command system

### 4.1 Universal rule

In every phase **except `chat`**, the input handler requires `value.startswith(":")`.
If a user types something that doesn't start with `:`, the status bar
shows:

```
  Unknown command (commands start with `:`)
```

and the input is cleared. No silent fallback, no path guessing.

Chat phase is the exception: free-form text (that isn't a `:` command
handled first) is sent to the LLM as a user turn.

### 4.2 `select` phase commands

| Command | Behavior |
|---|---|
| `:book N` | enter chat for book at 1-based index N. Comma lists (`:book 1,3`) are rejected with "multi-select not supported yet". Validate N is a digit, in range. |
| `:compile <path>` | resolve path (`_clean_path` keeps quote stripping). Switch to `index_select` phase with that path as `_compile_source`. Path may be absolute or relative to CWD. |
| `:delete N` | existing behavior (unchanged) |
| `:quit` | exit |
| `:back` | no-op in select (or hint "already at book list") |
| unknown `:foo` | status "Unknown command: :foo" |
| non-`:` | status "Unknown command (commands start with `:`)" |

### 4.3 `index_select` phase commands

See §3 table.

### 4.4 `compile` phase commands

Only `:quit` is accepted. Any other input is rejected with "please wait…
compile in progress."

### 4.5 `chat` phase commands

Existing commands are kept verbatim:

| Command | Behavior |
|---|---|
| `:quit` / `:q` / `:exit` | exit |
| `:back` / `:select` | return to select phase, refresh book list |
| `:clear` | clear chat log |
| `:addindex X` / `:addidx X` | start incremental build of index X via `ReplContext.add_index` |
| `:rmindex X` / `:rmidx X` | remove index X via `ReplContext.remove_index` |
| other non-`:` text | sent as user turn to the reading agent |
| `:foo` not in the list above | status "Unknown chat command: :foo" |

The **`.pdf` / `.epub` suffix autodetect** branch in `_handle_chat_input`
is deleted. Adding a book from chat always goes via `:back` → select →
`:compile <path>`.

### 4.6 Error visibility

Errors go to the status bar (one-liners) and the existing
`#error_display` Static (multi-line / stack traces). No new widget.

## §5 — Phase-specific header hints

`_header_hint_for_phase` is updated to reflect the new command vocabulary:

| Phase | Hint |
|---|---|
| `select` | `:book N  read    :compile <path>  add    :delete N  remove    :quit  quit` |
| `index_select` | `Space/Enter  toggle    :go  build    :back  cancel` |
| `compile` | `compiling…` |
| `chat` | `:back  books    :clear  clear    :addindex X / :rmindex X  indexes    :quit  quit` |

Hints use the same dim grey `#666666` color as today.

## §6 — Removed code

| Symbol / block | Reason |
|---|---|
| `Input(placeholder="> ", …)` | replaced by `#prompt_prefix` Static outside the Input |
| `_render_index_select` static-string layout | replaced by Checkbox widget labels |
| `_handle_index_select_input` letter-toggle branch | replaced by Checkbox.Changed handler |
| `_handle_chat_input` `.pdf / .epub` suffix branch | command vocabulary now explicit |
| `_handle_select_input` "free-form path" branch | replaced by `:compile <path>` |
| `_handle_select_input` "pure digit" branch | replaced by `:book N` |

## §7 — Testing

### Unit / behavior tests (python/tests)

The existing TUI tests (if any) drive the app through `run_test` and check
phase transitions. New cases:

- `test_index_provider_description_field`: build a frozen IndexProvider with
  `description="x"` and without — both succeed; the default is `""`.
- `test_tui_rejects_non_command_in_select`: submitting `hello` in
  `select_input` sets status to the "Unknown command (commands start with
  `:`)" text and does **not** change `phase`.
- `test_tui_book_command_enters_chat`: `:book 1` in a populated select
  phase transitions `phase == "chat"`.
- `test_tui_book_multi_rejected`: `:book 1,2` sets an error status and
  stays in select.
- `test_tui_compile_command_enters_index_select`: `:compile /tmp/x.epub`
  transitions `phase == "index_select"` with the right `_compile_source`.
- `test_tui_index_select_checkbox_toggle`: toggling `#cb_graph` adds
  `"graph"` to `_selected_index_types`; toggling again removes it.
- `test_tui_index_select_go_starts_compile`: with chunk+summary selected,
  pressing Enter (empty input) kicks off `_start_compile` with
  `index_types={"chunk","summary"}`.
- `test_tui_chat_path_no_longer_triggers_compile`: in chat, submitting
  `some.epub` runs the chat worker instead of switching phase.

Where existing tests assume the old behavior, update them to the new
command form (no new behavior is silently broken — the tests assert the
new intent).

### Manual verification

1. Launch `uv run bookscout-repl tui --config config.yaml`.
2. Book list renders, `>` prefix is visible beside the input, *not*
   disappearing when typing.
3. Type `1` alone — status shows "Unknown command (commands start with
   `:`)".
4. Type `:book 1` — enter chat with that book.
5. `:back` returns to select.
6. `:compile path/to/book.epub` enters `index_select`:
   - Checkboxes render with bold name + dim description.
   - Mouse click toggles; Tab + Space toggles.
   - Status updates to show current selection each toggle.
7. Press Enter on empty input — compile begins; progress panel renders as
   before; success returns to select list with the new book and correct
   `[csg]` / `[cs-]` flags.
8. `:back` from index_select returns to select without compiling.
9. In chat, typing `something.epub` sends the string to the LLM rather than
   triggering compile.

## §8 — Out of scope

- Natural-language parsing of bare input → commands. Future work only.
- Multi-book chat (`:book 1,3`): explicitly deferred per user decision.
  The handler rejects comma-form with a clear error so the UX doesn't
  silently mishalf-implement it.
- A persistent `:` history / command palette / Tab-completion of commands.
  The current UX is a few well-known commands; autocomplete is overkill.
- Adding `:help` command listing available commands per phase. Trivial to
  add later; not required for the redesign.

## §9 — Migration impact

- `IndexProvider` gains a defaulted field — all existing instantiations
  continue to work. Existing tests that build a provider without
  `description` still pass.
- TUI is the only consumer that renders `description`; server.py / Repl
  CLI are untouched.
- Tests that exercise the old "type digit / type path" select input are
  updated to use `:book N` / `:compile <path>`.
- No database schema change. No ReadingMode / toolset change. No new
  package.

## §10 — File change surface

| Package | File | Change |
|---|---|---|
| bookscout-doccompiler | `bookscout/doccompiler/index_provider.py` | add `description: str = ""` field |
| bookscout-index-summary | `bookscout/index/summary/provider.py` | set `description="…" ` |
| bookscout-index-chunk | `bookscout/index/chunk/provider.py` | set `description="…"` |
| bookscout-index-graph | `bookscout/index/graph/provider.py` | set `description="…"` |
| bookscout-repl | `bookscout/repl/tui.py` | rewrite compose() for Horizontal input+prompt, three Checkboxes in index_select, add Checkbox.Changed handler, replace `_handle_select_input` / `_handle_index_select_input` / `_handle_chat_input` with command-driven variants, remove path-suffix autodetect in chat, update `_header_hint_for_phase` strings |
| python/tests | existing tui tests if present, new cases per §7 | add new cases, migrate old ones to `:book` / `:compile` |

No changes to: `ReplContext`, `ReadingMode`, `ReadingAgentToolset`,
`Compiler`, `TaskManager`, `BooksStore`, MCP server.
