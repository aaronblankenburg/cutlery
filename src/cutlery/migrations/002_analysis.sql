-- State of the incremental analysis (cutter analyze) per project.
ALTER TABLE projects ADD COLUMN analysis_state TEXT NOT NULL DEFAULT 'idle';   -- idle, waiting, running, failed
ALTER TABLE projects ADD COLUMN analysis_message TEXT;
ALTER TABLE projects ADD COLUMN analysis_updated TEXT;
