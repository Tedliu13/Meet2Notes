# Meeting library and quick actions

Available in Meet2Notes 0.7.0. Search, tags and saved action templates are stored
locally; they do not require an AI model or a cloud account.

## Find a meeting or a spoken phrase

Open **Meetings** and enter words in **Search the library**. Choose titles and
descriptions, transcripts only, or both. A tag filter can be combined with the
search. Results are paginated, and the filters are preserved in the page URL.

Transcript search uses SQLite full-text search on the **active, completed**
transcription of each meeting. All search words must occur in the same segment;
punctuation is ignored and accented words can be found without their accents.
This is word matching, not semantic search or a cross-segment phrase search.
Title and description search matches the entered substring.

Each matching meeting shows up to three ranked excerpts. Click an excerpt to
open the transcript at that segment, highlight it and position the audio at its
timestamp. Playback does not start automatically. Meetings whose audio was
deleted remain searchable. If the active version changes before you open a
result, search again to obtain a link to the current version.

Saved segment edits immediately update the full-text index. Switching the active
transcript changes which version is searched; obsolete versions do not duplicate
the results. Search works independently of the assistant's semantic RAG index.

## Organize with tags

Use **Edit tags** in the library or an open meeting. Create a tag, select the
tags for this meeting and choose **Save tags**. Tags are shared across the local
library, and their counts show how many meetings use them. Clicking a tag filters
the library. Up to 30 tags can be assigned to one meeting.

Tag names have 1–40 characters. Whitespace and Unicode compatibility forms are
normalized; names that differ only in letter case cannot create duplicates.
Creating, renaming or deleting a tag takes effect immediately across the library.
**Cancel** discards only the pending selection for the current meeting. Deleting
a tag removes its associations, not its meetings or transcripts.

## Reuse assistant actions

The **Meeting Assistant**, including the **Prompt** page, offers four built-in
actions: follow-up email, decisions and risks, action items, and executive brief.
Choose an action and click **Use** to insert its prompt into the question box.
Review or edit the question, verify the meeting scope, and send it when ready.
Nothing is emailed or posted by these actions.

Use **Customize** to save a personal action, edit an existing one or make a copy
of a built-in prompt. Up to 100 custom actions are saved in the local database,
with names up to 80 characters and prompts up to 4,000 characters. They survive
browser reloads and app restarts. Built-in actions follow the interface language;
custom prompts keep the text you wrote. **Copy answer** copies the plain response.

Answer generation uses the configured AI provider and the assistant's current
scope. Search retrieval selects relevant context, so it is not an exhaustive
audit of every statement. For a complete short-meeting overview, select that
meeting and use **Context** to attach the full transcript, within the
model's context limit. Check generated owners, dates and commitments against the
source. Changing meeting scope clears the current conversation and attachments
so earlier meeting context is not carried into the next answer.

## Storage, upgrades and API

Migration `013_library.sql` adds `meeting_tags`, `meeting_tag_links` and
`assistant_actions` to `app.db`. Existing meetings and transcripts are retained.
Back up the data directory before upgrading, with the application stopped or
using SQLite's online backup API. Keep that backup if rolling back to an older
application version.

The local API exposes:

| Endpoint | Behavior |
| --- | --- |
| `GET /api/library` | `query`, `scope`, `tag_id`, `limit` and `offset`; returns meetings, tags, excerpts and total count |
| `GET/POST /api/tags` | List or create shared tags |
| `PATCH/DELETE /api/tags/{id}` | Rename or delete a shared tag |
| `GET/PUT /api/meetings/{id}/tags` | Read or atomically replace a meeting's tag IDs |
| `GET/POST /api/assistant/actions` | List or create personal prompt templates |
| `PATCH/DELETE /api/assistant/actions/{id}` | Edit or delete a personal prompt template |

These endpoints have the same local-server security boundary as the rest of the
application. See [Privacy](privacy.md) for provider/network behavior.
