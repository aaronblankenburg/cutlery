-- The user's notes for one cut, given when the job is started ("notes for this cut"); part of the prompt.
ALTER TABLE jobs ADD COLUMN note TEXT;
