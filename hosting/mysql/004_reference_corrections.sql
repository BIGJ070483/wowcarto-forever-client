-- Additive community corrections linked to immutable historical identifiers.
CREATE TABLE wcf_reference_generation (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  generation BIGINT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT INTO wcf_reference_generation(id,generation) VALUES (1,0);
CREATE TABLE wcf_reference_edits (
  locale VARCHAR(16) CHARACTER SET ascii NOT NULL,
  native_id INT UNSIGNED NOT NULL,
  revision INT UNSIGNED NOT NULL,
  name VARCHAR(200) NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY(locale,native_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE wcf_reference_history (
  locale VARCHAR(16) CHARACTER SET ascii NOT NULL,
  native_id INT UNSIGNED NOT NULL,
  revision INT UNSIGNED NOT NULL,
  name VARCHAR(200) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(locale,native_id,revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT INTO wcf_schema_versions(version) VALUES (4);
