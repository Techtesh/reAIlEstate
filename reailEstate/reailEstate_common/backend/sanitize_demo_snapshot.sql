\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF current_database() !~ '^demo_export_[a-f0-9]{32}$' THEN
        RAISE EXCEPTION 'Sanitization is restricted to an exporter-created temporary database';
    END IF;
END $$;

-- Enumerate dependent personal tables explicitly; never cascade into project evidence.
TRUNCATE
    auth_user_groups, auth_user_user_permissions, django_admin_log, django_session,
    platform_api_organizationmembership, platform_api_consent,
    platform_api_requirementprofile, platform_api_requirementversion,
    platform_api_frequentplace, platform_api_profileshare,
    platform_api_recommendationsnapshot, platform_api_recommendationitem,
    platform_api_shortlist, platform_api_shortlistproject,
    platform_api_comparisonshare, platform_api_notificationsubscription,
    platform_api_idempotencyrecord, platform_api_travelestimate,
    platform_api_queueworker, platform_api_operation, platform_api_deletionrecord,
    platform_api_outboxmessage, platform_api_inboxmessage;

CREATE TEMP TABLE demo_original_user_ids AS SELECT id FROM auth_user;
-- Re-exporting a restored demo must also work without a username collision.
UPDATE auth_user SET username='demo-export-old-' || gen_random_uuid()::text
WHERE username='demo-evidence-placeholder';
INSERT INTO auth_user
    (password, last_login, is_superuser, username, first_name, last_name,
     email, is_staff, is_active, date_joined)
VALUES
    ('!', NULL, false, 'demo-evidence-placeholder', '', '', '', false, false, CURRENT_TIMESTAMP);

DO $$
DECLARE
    placeholder_id integer;
    reference record;
BEGIN
    SELECT id INTO STRICT placeholder_id FROM auth_user WHERE username='demo-evidence-placeholder';
    FOR reference IN
        SELECT c.conrelid::regclass AS table_name, a.attname AS column_name, a.attnotnull
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
        WHERE c.contype='f' AND c.confrelid='auth_user'::regclass
    LOOP
        IF reference.attnotnull THEN
            EXECUTE format('UPDATE %s SET %I=$1', reference.table_name, reference.column_name)
                USING placeholder_id;
        ELSE
            EXECUTE format('UPDATE %s SET %I=NULL', reference.table_name, reference.column_name);
        END IF;
    END LOOP;
END $$;
DELETE FROM auth_user WHERE id IN (SELECT id FROM demo_original_user_ids);

-- Audit details may contain old authentication payloads, so retain events but not metadata.
DELETE FROM platform_api_auditevent WHERE lower(entity_type) IN
    ('user', 'auth.user', 'requirementprofile', 'requirementversion', 'shortlist',
     'comparisonshare', 'consent', 'notificationsubscription', 'organizationmembership');
UPDATE platform_api_auditevent SET metadata='{}'::jsonb;

CREATE FUNCTION pg_temp.demo_scrub_identity(value jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE result jsonb;
BEGIN
    IF jsonb_typeof(value)='object' THEN
        SELECT coalesce(jsonb_object_agg(key,
            CASE WHEN lower(replace(key, '_', ''))='userids' THEN '[]'::jsonb
                 ELSE pg_temp.demo_scrub_identity(item) END), '{}'::jsonb)
        INTO result FROM jsonb_each(value) AS entry(key, item)
        WHERE lower(replace(key, '_', '')) NOT IN
            ('user', 'userid', 'username', 'email', 'emailaddress', 'firstname', 'lastname',
             'password', 'passwordhash', 'accesstoken', 'refreshtoken', 'sessionkey',
             'actor', 'actorid', 'owner', 'ownerid', 'submittedby', 'uploadedby',
             'reviewedby', 'assignedto', 'markedby', 'mergedby', 'createdby', 'updatedby', 'requestedby');
        RETURN result;
    ELSIF jsonb_typeof(value)='array' THEN
        SELECT coalesce(jsonb_agg(pg_temp.demo_scrub_identity(item) ORDER BY position), '[]'::jsonb)
        INTO result FROM jsonb_array_elements(value) WITH ORDINALITY AS entry(item, position);
        RETURN result;
    END IF;
    RETURN value;
END $$;

DO $$
DECLARE field record;
BEGIN
    FOR field IN SELECT table_schema,table_name,column_name FROM information_schema.columns
        WHERE table_schema='public' AND udt_name='jsonb'
    LOOP
        EXECUTE format('UPDATE %I.%I SET %I=pg_temp.demo_scrub_identity(%I)',
            field.table_schema,field.table_name,field.column_name,field.column_name);
    END LOOP;
    IF (SELECT count(*) FROM auth_user) <> 1 OR EXISTS (
        SELECT 1 FROM auth_user WHERE password <> '!' OR is_active OR is_staff OR is_superuser
            OR username <> 'demo-evidence-placeholder' OR email <> '' OR first_name <> '' OR last_name <> ''
    ) THEN
        RAISE EXCEPTION 'Demo account sanitization failed';
    END IF;
END $$;
SET CONSTRAINTS ALL IMMEDIATE;
COMMIT;
