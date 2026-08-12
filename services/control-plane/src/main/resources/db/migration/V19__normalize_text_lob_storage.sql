-- Hibernate maps String fields annotated with @Lob to PostgreSQL large objects.
-- These columns are TEXT, so older writes stored the large-object OID as text.
-- Decode those references in place before the entities switch to normal String mapping.
CREATE FUNCTION pg_temp.apf_decode_legacy_text_lob(lob_ref TEXT)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
BEGIN
    IF lob_ref ~ '^[0-9]+$'
       AND EXISTS (
           SELECT 1
           FROM pg_largeobject_metadata
           WHERE oid = lob_ref::oid
       ) THEN
        RETURN convert_from(lo_get(lob_ref::oid), 'UTF8');
    END IF;
    RETURN lob_ref;
END;
$$;

UPDATE workflows
SET draft_dsl_json = pg_temp.apf_decode_legacy_text_lob(draft_dsl_json),
    draft_layout_json = pg_temp.apf_decode_legacy_text_lob(draft_layout_json)
WHERE draft_dsl_json ~ '^[0-9]+$' OR draft_layout_json ~ '^[0-9]+$';

UPDATE workflow_versions
SET dsl_json = pg_temp.apf_decode_legacy_text_lob(dsl_json),
    layout_json = pg_temp.apf_decode_legacy_text_lob(layout_json)
WHERE dsl_json ~ '^[0-9]+$' OR layout_json ~ '^[0-9]+$';

UPDATE agents
SET instructions = pg_temp.apf_decode_legacy_text_lob(instructions)
WHERE instructions ~ '^[0-9]+$';

UPDATE messages
SET content_json = pg_temp.apf_decode_legacy_text_lob(content_json)
WHERE content_json ~ '^[0-9]+$';

UPDATE runs
SET input_payload_json = pg_temp.apf_decode_legacy_text_lob(input_payload_json),
    output_payload_json = pg_temp.apf_decode_legacy_text_lob(output_payload_json),
    citations_json = pg_temp.apf_decode_legacy_text_lob(citations_json),
    metrics_json = pg_temp.apf_decode_legacy_text_lob(metrics_json)
WHERE input_payload_json ~ '^[0-9]+$'
   OR output_payload_json ~ '^[0-9]+$'
   OR citations_json ~ '^[0-9]+$'
   OR metrics_json ~ '^[0-9]+$';

UPDATE run_events
SET payload_json = pg_temp.apf_decode_legacy_text_lob(payload_json)
WHERE payload_json ~ '^[0-9]+$';

UPDATE run_node_attempts
SET details_json = pg_temp.apf_decode_legacy_text_lob(details_json)
WHERE details_json ~ '^[0-9]+$';

UPDATE run_node_executions
SET details_json = pg_temp.apf_decode_legacy_text_lob(details_json)
WHERE details_json ~ '^[0-9]+$';

UPDATE skills
SET draft_definition_json = pg_temp.apf_decode_legacy_text_lob(draft_definition_json)
WHERE draft_definition_json ~ '^[0-9]+$';

UPDATE skill_versions
SET definition_json = pg_temp.apf_decode_legacy_text_lob(definition_json)
WHERE definition_json ~ '^[0-9]+$';

UPDATE mcp_connections
SET tools_json = pg_temp.apf_decode_legacy_text_lob(tools_json)
WHERE tools_json ~ '^[0-9]+$';
