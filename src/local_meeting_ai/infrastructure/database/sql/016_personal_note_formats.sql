ALTER TABLE summary_templates ADD COLUMN owner_id INTEGER REFERENCES users(id);
CREATE INDEX summary_templates_owner ON summary_templates(owner_id);
CREATE TABLE user_note_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    template_id INTEGER REFERENCES summary_templates(id) ON DELETE SET NULL
);
