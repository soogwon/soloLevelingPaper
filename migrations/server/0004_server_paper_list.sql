CREATE INDEX IF NOT EXISTS idx_server_papers_owner_stable_list
    ON server_papers(owner_user_id, paper_id);
