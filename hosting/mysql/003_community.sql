-- Community edits: additive migration; do not run against an unbacked production database.
CREATE TABLE wcf_community_imports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  sha256 BINARY(32) NOT NULL UNIQUE,
  raw_file MEDIUMBLOB NOT NULL,
  record_count INT UNSIGNED NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE wcf_community_annotations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  import_id BIGINT UNSIGNED NOT NULL,
  ordinal INT UNSIGNED NOT NULL,
  revision INT UNSIGNED NOT NULL DEFAULT 1,
  raw_record MEDIUMBLOB NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_community_import_ordinal (import_id, ordinal),
  CONSTRAINT fk_community_import FOREIGN KEY (import_id) REFERENCES wcf_community_imports(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE wcf_community_revisions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  annotation_id BIGINT UNSIGNED NOT NULL,
  revision INT UNSIGNED NOT NULL,
  raw_record MEDIUMBLOB NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_community_revision (annotation_id, revision),
  CONSTRAINT fk_community_annotation FOREIGN KEY (annotation_id) REFERENCES wcf_community_annotations(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT INTO wcf_schema_versions(version) VALUES (3);
