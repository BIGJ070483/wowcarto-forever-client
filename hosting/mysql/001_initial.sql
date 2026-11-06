-- WoWCarto Forever: initial empty schema, MySQL 5.7+, InnoDB.
-- Select the intended database in phpMyAdmin before executing.
-- No credentials, accounts, native files or annotations are included.
-- Deliberately fails if tables already exist; never resets existing data.
CREATE TABLE wcf_schema_versions (
  version SMALLINT UNSIGNED NOT NULL PRIMARY KEY,
  installed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE wcf_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL,
  password_hash VARBINARY(255) NOT NULL,
  role ENUM('member','moderator','admin') NOT NULL DEFAULT 'member',
  enabled TINYINT UNSIGNED NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_wcf_members_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE wcf_imports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  member_id BIGINT UNSIGNED NOT NULL,
  sha256 BINARY(32) NOT NULL,
  raw_file MEDIUMBLOB NOT NULL,
  record_count INT UNSIGNED NOT NULL,
  native_format VARCHAR(16) CHARACTER SET ascii NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_wcf_imports_member_digest (member_id, sha256),
  CONSTRAINT fk_wcf_imports_member FOREIGN KEY (member_id)
    REFERENCES wcf_members(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE wcf_annotations (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  import_id BIGINT UNSIGNED NOT NULL,
  ordinal INT UNSIGNED NOT NULL,
  raw_record MEDIUMBLOB NOT NULL,
  review_state ENUM('pending','accepted','rejected') NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_wcf_annotations_position (import_id, ordinal),
  KEY ix_wcf_annotations_review (review_state, id),
  CONSTRAINT fk_wcf_annotations_import FOREIGN KEY (import_id)
    REFERENCES wcf_imports(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE wcf_moderation_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  annotation_id BIGINT UNSIGNED NOT NULL,
  moderator_id BIGINT UNSIGNED NOT NULL,
  decision ENUM('pending','accepted','rejected') NOT NULL,
  reason TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_wcf_moderation_annotation (annotation_id, id),
  CONSTRAINT fk_wcf_moderation_annotation FOREIGN KEY (annotation_id)
    REFERENCES wcf_annotations(id) ON DELETE RESTRICT,
  CONSTRAINT fk_wcf_moderation_member FOREIGN KEY (moderator_id)
    REFERENCES wcf_members(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO wcf_schema_versions(version) VALUES (1);
