package com.agentpowflow.controlplane.migration;

import com.agentpowflow.controlplane.approvals.ApprovalAlertDeliveryEntity;
import com.agentpowflow.controlplane.approvals.ApprovalAlertDeliveryRepository;
import com.agentpowflow.controlplane.approvals.ApprovalAlertEntity;
import com.agentpowflow.controlplane.approvals.ApprovalAlertRepository;
import com.agentpowflow.controlplane.runs.RunApprovalEntity;
import com.agentpowflow.controlplane.runs.RunApprovalRepository;
import com.agentpowflow.controlplane.runs.RunApprovalAuditFactEntity;
import com.agentpowflow.controlplane.runs.RunApprovalAuditFactRepository;
import com.agentpowflow.controlplane.runs.RunEntity;
import com.agentpowflow.controlplane.runs.RunRepository;
import com.agentpowflow.controlplane.knowledge.KnowledgeBaseRepository;
import com.agentpowflow.controlplane.tasks.TaskRepository;
import com.agentpowflow.controlplane.tasks.TaskEntity;
import com.agentpowflow.controlplane.tasks.TaskDispatchOutboxEntity;
import com.agentpowflow.controlplane.tasks.TaskDispatchOutboxRepository;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.MigrationVersion;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest
@EnabledIfEnvironmentVariable(named = "CONTROL_TEST_POSTGRES_JDBC_URL", matches = ".+")
class ControlSchemaMigrationIntegrationTest {
    private static final String JDBC_URL = System.getenv("CONTROL_TEST_POSTGRES_JDBC_URL");
    private static final String DB_USER = envOrDefault("CONTROL_TEST_POSTGRES_USER", "apf");
    private static final String DB_PASSWORD = envOrDefault("CONTROL_TEST_POSTGRES_PASSWORD", "apf");
    private static final String CONTEXT_SCHEMA = schemaName();
    private static volatile boolean contextSchemaCreated;

    @Autowired private RunRepository runRepository;
    @Autowired private RunApprovalRepository approvalRepository;
    @Autowired private RunApprovalAuditFactRepository approvalAuditRepository;
    @Autowired private ApprovalAlertRepository approvalAlertRepository;
    @Autowired private ApprovalAlertDeliveryRepository approvalAlertDeliveryRepository;
    @Autowired private KnowledgeBaseRepository knowledgeBaseRepository;
    @Autowired private TaskRepository taskRepository;
    @Autowired private TaskDispatchOutboxRepository taskDispatchOutboxRepository;

    @DynamicPropertySource
    static void databaseProperties(DynamicPropertyRegistry registry) {
        createSchema(CONTEXT_SCHEMA);
        contextSchemaCreated = true;
        registry.add("spring.datasource.url", () -> JDBC_URL);
        registry.add("spring.datasource.username", () -> DB_USER);
        registry.add("spring.datasource.password", () -> DB_PASSWORD);
        registry.add("spring.jpa.hibernate.ddl-auto", () -> "validate");
        registry.add("spring.jpa.properties.hibernate.default_schema", () -> CONTEXT_SCHEMA);
        registry.add("spring.datasource.hikari.schema", () -> CONTEXT_SCHEMA);
        registry.add("spring.flyway.schemas", () -> CONTEXT_SCHEMA);
        registry.add("spring.flyway.default-schema", () -> CONTEXT_SCHEMA);
        registry.add("spring.flyway.baseline-on-migrate", () -> "true");
        registry.add("spring.flyway.baseline-version", () -> "0");
        registry.add("control-plane.execution-dispatch.enabled", () -> "false");
        registry.add("control-plane.task-dispatch.enabled", () -> "false");
        registry.add("control-plane.approvals.timeout-scan-enabled", () -> "false");
        registry.add("control-plane.approval-alerts.evaluation-enabled", () -> "false");
        registry.add("control-plane.approval-alerts.delivery-enabled", () -> "false");
    }

    @AfterAll
    static void cleanupContextSchema() {
        if (contextSchemaCreated) {
            dropSchema(CONTEXT_SCHEMA);
        }
    }

    @Test
    void emptySchemaMigratesThroughV20AndHibernateValidatesIt() throws SQLException {
        assertEquals(expectedVersions(1), successfulVersions(CONTEXT_SCHEMA));
        assertTrue(tableExists(CONTEXT_SCHEMA, "tenants"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "runs"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "run_node_attempts"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "run_approvals"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "approval_audit_facts"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "approval_alerts"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "approval_alert_deliveries"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "knowledge_bases"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "kb_tenant_bindings"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "tasks"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "task_events"));
        assertTrue(tableExists(CONTEXT_SCHEMA, "task_dispatch_outbox"));
        assertEquals(0, knowledgeBaseRepository.count());
        assertEquals(0, taskRepository.count());
    }

    @Test
    void nonEmptySharedSchemaBaselinesAtZeroThenAppliesEveryMigration() throws SQLException {
        String schema = schemaName();
        createSchema(schema);
        try {
            execute(schema, "CREATE TABLE unrelated_execution_table (id INTEGER PRIMARY KEY)");
            flyway(schema, null).migrate();
            assertEquals("0," + expectedVersions(1), successfulVersions(schema));
            assertTrue(tableExists(schema, "unrelated_execution_table"));
            assertTrue(tableExists(schema, "workflows"));
            assertTrue(tableExists(schema, "run_node_executions"));
            assertTrue(tableExists(schema, "run_approvals"));
            assertTrue(tableExists(schema, "approval_audit_facts"));
            assertTrue(tableExists(schema, "approval_alerts"));
            assertTrue(tableExists(schema, "knowledge_bases"));
            assertTrue(tableExists(schema, "tasks"));
        } finally {
            dropSchema(schema);
        }
    }

    @Test
    void existingV1SchemaUpgradesDeterministicallyThroughV20() throws SQLException {
        String schema = schemaName();
        createSchema(schema);
        try {
            flyway(schema, MigrationVersion.fromVersion("1")).migrate();
            assertEquals("1", successfulVersions(schema));
            flyway(schema, null).migrate();
            assertEquals(expectedVersions(1), successfulVersions(schema));
            assertTrue(tableExists(schema, "run_dispatch_outbox"));
            assertTrue(tableExists(schema, "workflow_secrets"));
            assertTrue(tableExists(schema, "task_dispatch_outbox"));
            assertTrue(tableExists(schema, "run_node_attempts"));
            assertTrue(tableExists(schema, "run_approvals"));
            assertTrue(tableExists(schema, "approval_audit_facts"));
            assertTrue(tableExists(schema, "approval_alert_deliveries"));
            assertTrue(tableExists(schema, "knowledge_bases"));
            assertTrue(tableExists(schema, "tasks"));
        } finally {
            dropSchema(schema);
        }
    }

    @Test
    void approvalVisibilityAndStatsFilterRolesBeforeAggregationAndLimit() {
        runRepository.save(run("run-visible"));
        runRepository.save(run("run-hidden"));
        approvalRepository.save(approval("approval-visible", "run-visible", "TENANT_ADMIN"));
        approvalRepository.save(approval("approval-hidden", "run-hidden", "AUDITOR"));

        List<RunApprovalEntity> visible = approvalRepository.findVisible(
                "tenant-stats", "PENDING", false, List.of("TENANT_ADMIN")
        );
        RunApprovalRepository.ApprovalStatsProjection assignedStats = approvalRepository.summarizeVisible(
                "tenant-stats", false, List.of("TENANT_ADMIN"), Instant.now(),
                Instant.now().plusSeconds(900), Instant.now().minusSeconds(86400)
        );
        RunApprovalRepository.ApprovalStatsProjection platformStats = approvalRepository.summarizeVisible(
                "tenant-stats", true, List.of("PLATFORM_ADMIN"), Instant.now(),
                Instant.now().plusSeconds(900), Instant.now().minusSeconds(86400)
        );

        assertEquals(1, visible.size());
        assertEquals("approval-visible", visible.getFirst().getApprovalId());
        assertEquals(1, assignedStats.getPendingCount());
        assertEquals(2, platformStats.getPendingCount());
    }

    @Test
    void approvalAuditFactsRemainQueryableAfterRunDeletion() {
        RunEntity run = run("run-retained-audit");
        runRepository.saveAndFlush(run);
        RunApprovalEntity approval = approval("approval-retained-audit", run.getRunId(), "TENANT_ADMIN");
        approvalRepository.saveAndFlush(approval);

        RunApprovalAuditFactEntity fact = new RunApprovalAuditFactEntity();
        fact.setApprovalId(approval.getApprovalId());
        fact.setTenantId(approval.getTenantId());
        fact.setRunId(approval.getRunId());
        fact.setNodeId(approval.getNodeId());
        fact.setAssigneeRolesJson(approval.getAssigneeRolesJson());
        fact.setStatus("DECIDED");
        fact.setDecision("APPROVE");
        fact.setCreatedAt(Instant.now().minusSeconds(120));
        fact.setTerminalAt(Instant.now());
        fact.setDecisionDurationSeconds(120L);
        approvalAuditRepository.saveAndFlush(fact);

        ApprovalAlertEntity alert = new ApprovalAlertEntity();
        alert.setAlertId("alert-retained-audit");
        alert.setDedupeKey("approval:approval-retained-audit:overdue");
        alert.setTenantId("tenant-stats");
        alert.setApprovalId(approval.getApprovalId());
        alert.setRunId(run.getRunId());
        alert.setNodeId("approval-node");
        alert.setAlertType("OVERDUE");
        alert.setSeverity("CRITICAL");
        alert.setMessage("Approval is overdue");
        alert.setAssigneeRolesJson("[\"TENANT_ADMIN\"]");
        approvalAlertRepository.saveAndFlush(alert);
        ApprovalAlertDeliveryEntity delivery = new ApprovalAlertDeliveryEntity();
        delivery.setDeliveryId("delivery-retained-audit");
        delivery.setAlertId(alert.getAlertId());
        delivery.setTenantId("tenant-stats");
        delivery.setPayloadJson("{\"event_id\":\"alert-retained-audit\"}");
        delivery.setStatus("DISABLED");
        delivery.setMaxAttempts(3);
        approvalAlertDeliveryRepository.saveAndFlush(delivery);

        runRepository.delete(run);
        runRepository.flush();

        List<RunApprovalAuditFactRepository.ApprovalTrendProjection> trend =
                approvalAuditRepository.summarizeDaily(
                        "tenant-stats", false, List.of("TENANT_ADMIN"),
                        LocalDate.now(ZoneOffset.UTC), LocalDate.now(ZoneOffset.UTC)
                );
        assertTrue(approvalRepository.findById(approval.getApprovalId()).isEmpty());
        assertTrue(approvalAuditRepository.findById(fact.getApprovalId()).isPresent());
        assertTrue(approvalAlertRepository.findById(alert.getAlertId()).isPresent());
        assertTrue(approvalAlertDeliveryRepository.findById(delivery.getDeliveryId()).isPresent());
        assertEquals(1, approvalAlertRepository.summarize(
                "tenant-stats", Instant.now().minusSeconds(86400)
        ).getOpenCount());
        assertEquals(1, trend.size());
        assertEquals(1, trend.getFirst().getDecidedCount());
        assertEquals(0, trend.getFirst().getTimedOutCount());
        assertEquals(120, trend.getFirst().getAverageDecisionSeconds());
    }

    @Test
    void taskDeletionCascadesItsDispatchOutbox() {
        String taskId = "task-cascade-" + UUID.randomUUID();
        TaskEntity task = new TaskEntity();
        task.setTaskId(taskId);
        task.setTenantId("tenant-cascade");
        task.setKbId("kb-cascade");
        task.setDocId("doc-cascade");
        task.setTaskType("DOC_INGEST");
        task.setStatus("PENDING");
        task.setStage("QUEUED");
        task.setFilename("cascade.txt");
        task.setStateOwner("CONTROL");
        taskRepository.saveAndFlush(task);

        TaskDispatchOutboxEntity outbox = new TaskDispatchOutboxEntity();
        outbox.setTenantId("tenant-cascade");
        outbox.setTaskId(taskId);
        outbox.setCommandType("DOC_INGEST_TEXT");
        outbox.setPayloadJson("{}");
        String outboxId = taskDispatchOutboxRepository.saveAndFlush(outbox).getOutboxId();

        taskRepository.deleteById(taskId);
        taskRepository.flush();

        assertTrue(taskDispatchOutboxRepository.findById(outboxId).isEmpty());
    }

    private RunEntity run(String runId) {
        RunEntity item = new RunEntity();
        item.setRunId(runId);
        item.setTenantId("tenant-stats");
        item.setConversationId("conversation-stats");
        item.setWorkflowId("workflow-stats");
        item.setWorkflowVersionId("version-stats");
        item.setWorkflowVersionNo(1);
        item.setWorkflowChecksum("a".repeat(64));
        item.setStatus("WAITING_APPROVAL");
        item.setStage("workflow.waiting_approval");
        item.setProgress(50);
        item.setInputPayloadJson("{}");
        item.setOutputPayloadJson("{}");
        item.setCitationsJson("[]");
        item.setMetricsJson("{}");
        item.setErrorCode("");
        item.setErrorMessage("");
        item.setCreatedBy("integration-test");
        return item;
    }

    private RunApprovalEntity approval(String approvalId, String runId, String role) {
        RunApprovalEntity item = new RunApprovalEntity();
        item.setApprovalId(approvalId);
        item.setTenantId("tenant-stats");
        item.setRunId(runId);
        item.setNodeId("approval-node");
        item.setNodeType("human.approval");
        item.setTitle("Integration approval");
        item.setInstructions("");
        item.setAssigneeRolesJson("[\"" + role + "\"]");
        item.setCheckpointJson("{\"version\":1,\"node_id\":\"approval-node\"}");
        item.setTimeoutAction("REJECT");
        item.setExpiresAt(Instant.now().plusSeconds(600));
        return item;
    }

    private static Flyway flyway(String schema, MigrationVersion target) {
        var config = Flyway.configure()
                .dataSource(JDBC_URL, DB_USER, DB_PASSWORD)
                .schemas(schema)
                .defaultSchema(schema)
                .baselineOnMigrate(true)
                .baselineVersion(MigrationVersion.fromVersion("0"))
                .validateOnMigrate(true)
                .locations("classpath:db/migration");
        if (target != null) {
            config.target(target);
        }
        return config.load();
    }

    private static String successfulVersions(String schema) throws SQLException {
        String query = "SELECT string_agg(version, ',' ORDER BY installed_rank) " +
                "FROM " + quoted(schema) + ".flyway_schema_history WHERE success";
        try (Connection connection = connection();
             Statement statement = connection.createStatement();
             ResultSet rows = statement.executeQuery(query)) {
            rows.next();
            return rows.getString(1);
        }
    }

    private static String expectedVersions(int first) {
        return java.util.stream.IntStream.rangeClosed(first, 20)
                .mapToObj(Integer::toString)
                .collect(java.util.stream.Collectors.joining(","));
    }

    private static boolean tableExists(String schema, String table) throws SQLException {
        try (Connection connection = connection();
             var statement = connection.prepareStatement(
                     "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=? AND table_name=?)")) {
            statement.setString(1, schema);
            statement.setString(2, table);
            try (ResultSet rows = statement.executeQuery()) {
                rows.next();
                return rows.getBoolean(1);
            }
        }
    }

    private static void execute(String schema, String statementSql) {
        try (Connection connection = connection(); Statement statement = connection.createStatement()) {
            statement.execute("SET search_path TO " + quoted(schema));
            statement.execute(statementSql);
        } catch (SQLException exception) {
            throw new IllegalStateException("failed to execute integration-test SQL", exception);
        }
    }

    private static void createSchema(String schema) {
        requireSafeSchema(schema);
        try (Connection connection = connection(); Statement statement = connection.createStatement()) {
            statement.execute("CREATE SCHEMA " + quoted(schema));
        } catch (SQLException exception) {
            throw new IllegalStateException("failed to create integration-test schema", exception);
        }
    }

    private static void dropSchema(String schema) {
        requireSafeSchema(schema);
        try (Connection connection = connection(); Statement statement = connection.createStatement()) {
            statement.execute("DROP SCHEMA " + quoted(schema) + " CASCADE");
        } catch (SQLException exception) {
            throw new IllegalStateException("failed to drop integration-test schema", exception);
        }
    }

    private static Connection connection() throws SQLException {
        return DriverManager.getConnection(JDBC_URL, DB_USER, DB_PASSWORD);
    }

    private static String schemaName() {
        return "apf_cp_it_" + UUID.randomUUID().toString().replace("-", "");
    }

    private static void requireSafeSchema(String schema) {
        if (schema == null || !schema.matches("apf_cp_it_[0-9a-f]{32}")) {
            throw new IllegalArgumentException("refusing to manage unsafe integration-test schema");
        }
    }

    private static String quoted(String identifier) {
        requireSafeSchema(identifier);
        return '"' + identifier + '"';
    }

    private static String envOrDefault(String key, String fallback) {
        String value = System.getenv(key);
        return value == null || value.isBlank() ? fallback : value.trim();
    }
}
