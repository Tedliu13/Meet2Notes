CREATE TABLE meeting_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE
);

CREATE TABLE meeting_tag_links (
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES meeting_tags(id) ON DELETE CASCADE,
    PRIMARY KEY (meeting_id, tag_id)
);
CREATE INDEX idx_meeting_tag_links_tag ON meeting_tag_links(tag_id, meeting_id);

CREATE TABLE assistant_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL
);
