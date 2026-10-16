-- Store the requested IDs, even if a voice profile is later deleted. An empty
-- candidate list retains legacy matching against all saved profiles; a stale
-- nonempty list must never silently expand to all profiles.
ALTER TABLE meetings ADD COLUMN known_speaker_profile_ids_json TEXT NOT NULL DEFAULT '[]';
