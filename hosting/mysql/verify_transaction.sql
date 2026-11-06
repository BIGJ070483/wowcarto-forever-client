-- Run only on an otherwise empty installation, in one phpMyAdmin submission.
-- No account is enabled; fixture writes are rolled back in this connection.
START TRANSACTION;
INSERT INTO wcf_members(username, password_hash)
VALUES ('WCF_INTERNAL_SCHEMA_CHECK', 'INVALID_DISABLED_FIXTURE');
SET @wcf_member = LAST_INSERT_ID();
INSERT INTO wcf_imports(member_id, sha256, raw_file, record_count, native_format)
VALUES (@wcf_member, UNHEX(SHA2('WCF_SCHEMA_FIXTURE',256)),
        'WCF_SCHEMA_FIXTURE', 2, 'test-only');
SET @wcf_import = LAST_INSERT_ID();
INSERT INTO wcf_annotations(import_id, ordinal, raw_record)
VALUES (@wcf_import, 1, 'IDENTICAL_FIXTURE_ROW'),
       (@wcf_import, 2, 'IDENTICAL_FIXTURE_ROW');
SELECT COUNT(*) AS preserved_rows, COUNT(DISTINCT ordinal) AS preserved_positions,
       COUNT(DISTINCT raw_record) AS distinct_payloads,
       SUM(review_state = 'pending') AS pending_rows
FROM wcf_annotations WHERE import_id = @wcf_import;
ROLLBACK;
SELECT (SELECT COUNT(*) FROM wcf_members) AS remaining_members,
       (SELECT COUNT(*) FROM wcf_imports) AS remaining_imports,
       (SELECT COUNT(*) FROM wcf_annotations) AS remaining_annotations,
       (SELECT COUNT(*) FROM wcf_moderation_events) AS remaining_events;
