-- Additive generation marker for consistent paginated community snapshots.
CREATE TABLE wcf_community_generation (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  generation BIGINT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT INTO wcf_community_generation(id,generation) VALUES (1,0);
INSERT INTO wcf_schema_versions(version) VALUES (5);
