package com.agentpowflow.controlplane.canvas;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DslValidatorTest {
    private DslValidator dslValidator;

    @BeforeEach
    void setUp() {
        NodeTypeRegistry registry = new NodeTypeRegistry();
        ConnectionRuleEngine ruleEngine = new ConnectionRuleEngine(registry);
        dslValidator = new DslValidator(registry, ruleEngine);
    }

    @Test
    void shouldPassForValidGraph() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "n1",
                List.of(
                        new CanvasModels.Node("n1", "start", Map.of()),
                        new CanvasModels.Node("n2", "llm.generate", Map.of()),
                        new CanvasModels.Node("n3", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "n1", "n2", null),
                        new CanvasModels.Edge("e2", "n2", "n3", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);

        assertTrue(result.errors().isEmpty());
    }

    @Test
    void shouldAcceptStructuredParallelForkAndDeterministicJoin() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("fork", "parallel.fork", Map.of()),
                        new CanvasModels.Node("draft", "llm.generate", Map.of()),
                        new CanvasModels.Node("review", "llm.generate", Map.of()),
                        new CanvasModels.Node("join", "parallel.join", Map.of("answer_branch", "draft")),
                        new CanvasModels.Node("end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "fork", null),
                        new CanvasModels.Edge("e2", "fork", "draft", "draft"),
                        new CanvasModels.Edge("e3", "fork", "review", "review"),
                        new CanvasModels.Edge("e4", "draft", "join", null),
                        new CanvasModels.Edge("e5", "review", "join", null),
                        new CanvasModels.Edge("e6", "join", "end", null)
                )
        );

        assertTrue(dslValidator.validate(dsl).valid());
    }

    @Test
    void shouldAcceptDurableHumanApprovalWithExplicitDecisionBranches() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("draft", "llm.generate", Map.of()),
                        new CanvasModels.Node("approval", "human.approval", Map.of(
                                "title", "Publish answer",
                                "instructions", "Review the generated answer.",
                                "assignee_roles", List.of("TENANT_ADMIN"),
                                "timeout_seconds", 3600,
                                "timeout_action", "reject"
                        )),
                        new CanvasModels.Node("approved", "end", Map.of()),
                        new CanvasModels.Node("rejected", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "draft", null),
                        new CanvasModels.Edge("e2", "draft", "approval", null),
                        new CanvasModels.Edge("e3", "approval", "approved", "approve"),
                        new CanvasModels.Edge("e4", "approval", "rejected", "reject")
                )
        );

        assertTrue(dslValidator.validate(dsl).valid());
    }

    @Test
    void shouldRejectInvalidHumanApprovalPolicyAndBranches() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("approval", "human.approval", Map.of(
                                "title", "", "assignee_roles", List.of(), "timeout_seconds", 10
                        )),
                        new CanvasModels.Node("end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "approval", null),
                        new CanvasModels.Edge("e2", "approval", "end", "approve")
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);
        assertTrue(result.errors().stream().anyMatch(issue -> "HUMAN_APPROVAL_CONFIG_INVALID".equals(issue.code())));
        assertTrue(result.errors().stream().anyMatch(issue -> "HUMAN_APPROVAL_BRANCH_INVALID".equals(issue.code())));
    }

    @Test
    void shouldRejectAmbiguousOrdinaryNodeOutgoingEdges() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("left", "llm.generate", Map.of()),
                        new CanvasModels.Node("right", "llm.generate", Map.of()),
                        new CanvasModels.Node("left-end", "end", Map.of()),
                        new CanvasModels.Node("right-end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "left", null),
                        new CanvasModels.Edge("e2", "start", "right", null),
                        new CanvasModels.Edge("e3", "left", "left-end", null),
                        new CanvasModels.Edge("e4", "right", "right-end", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);
        assertTrue(result.errors().stream().anyMatch(issue -> "NODE_OUTGOING_AMBIGUOUS".equals(issue.code())));
    }

    @Test
    void shouldRejectParallelJoinWithoutMatchingAnswerBranch() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("fork", "parallel.fork", Map.of()),
                        new CanvasModels.Node("a", "llm.generate", Map.of()),
                        new CanvasModels.Node("b", "llm.generate", Map.of()),
                        new CanvasModels.Node("join", "parallel.join", Map.of("answer_branch", "missing")),
                        new CanvasModels.Node("end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "fork", null),
                        new CanvasModels.Edge("e2", "fork", "a", "a"),
                        new CanvasModels.Edge("e3", "fork", "b", "b"),
                        new CanvasModels.Edge("e4", "a", "join", null),
                        new CanvasModels.Edge("e5", "b", "join", null),
                        new CanvasModels.Edge("e6", "join", "end", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);
        assertTrue(result.errors().stream().anyMatch(issue -> "PARALLEL_ANSWER_BRANCH_INVALID".equals(issue.code())));
    }

    @Test
    void shouldFailWhenGraphContainsCycle() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "n1",
                List.of(
                        new CanvasModels.Node("n1", "start", Map.of()),
                        new CanvasModels.Node("n2", "llm.generate", Map.of()),
                        new CanvasModels.Node("n3", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "n1", "n2", null),
                        new CanvasModels.Edge("e2", "n2", "n1", null),
                        new CanvasModels.Edge("e3", "n2", "n3", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);

        assertTrue(result.errors().stream().anyMatch(issue -> "CYCLE_DETECTED".equals(issue.code())));
    }

    @Test
    void shouldFailWhenConditionBranchMissing() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "n1",
                List.of(
                        new CanvasModels.Node("n1", "start", Map.of()),
                        new CanvasModels.Node("n2", "condition.if", Map.of()),
                        new CanvasModels.Node("n3", "llm.generate", Map.of()),
                        new CanvasModels.Node("n4", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "n1", "n2", null),
                        new CanvasModels.Edge("e2", "n2", "n3", "true"),
                        new CanvasModels.Edge("e3", "n3", "n4", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);

        assertTrue(result.errors().stream().anyMatch(issue -> "CONDITION_BRANCH_MISSING".equals(issue.code())));
    }

    @Test
    void shouldFailWhenSchemaMismatch() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "n1",
                List.of(
                        new CanvasModels.Node("n1", "start", Map.of()),
                        new CanvasModels.Node("n2", "end", Map.of())
                ),
                List.of(new CanvasModels.Edge("e1", "n1", "n2", null))
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);

        assertFalse(result.errors().isEmpty());
        assertTrue(result.errors().stream().anyMatch(issue -> "SCHEMA_FIELD_MISSING".equals(issue.code())));
    }

    @Test
    void shouldFailWhenPublishedGraphWouldContainAnUnreachableNode() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "n1",
                List.of(
                        new CanvasModels.Node("n1", "start", Map.of()),
                        new CanvasModels.Node("n2", "llm.generate", Map.of()),
                        new CanvasModels.Node("n3", "end", Map.of()),
                        new CanvasModels.Node("orphan", "llm.generate", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "n1", "n2", null),
                        new CanvasModels.Edge("e2", "n2", "n3", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);

        assertTrue(result.errors().stream().anyMatch(issue ->
                "NODE_UNREACHABLE".equals(issue.code()) && "orphan".equals(issue.nodeId())));
    }

    @Test
    void shouldFailClosedForANodeWithoutRuntimeAdapter() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "n1",
                List.of(
                        new CanvasModels.Node("n1", "start", Map.of()),
                        new CanvasModels.Node("n2", "code.execute", Map.of()),
                        new CanvasModels.Node("n3", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "n1", "n2", null),
                        new CanvasModels.Edge("e2", "n2", "n3", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);

        assertFalse(result.valid());
        assertTrue(result.errors().stream().anyMatch(issue -> "NODE_TYPE_UNKNOWN".equals(issue.code())));
    }

    @Test
    void shouldRejectInvalidConditionAndLlmRuntimeConfig() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("condition", "condition.if", Map.of("operator", "exec")),
                        new CanvasModels.Node("llm-true", "llm.generate", Map.of("temperature", 9)),
                        new CanvasModels.Node("llm-false", "llm.generate", Map.of("max_tokens", 0)),
                        new CanvasModels.Node("end-true", "end", Map.of()),
                        new CanvasModels.Node("end-false", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "condition", null),
                        new CanvasModels.Edge("e2", "condition", "llm-true", "true"),
                        new CanvasModels.Edge("e3", "condition", "llm-false", "false"),
                        new CanvasModels.Edge("e4", "llm-true", "end-true", null),
                        new CanvasModels.Edge("e5", "llm-false", "end-false", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);

        assertTrue(result.errors().stream().anyMatch(issue -> "CONDITION_OPERATOR_INVALID".equals(issue.code())));
        assertTrue(result.errors().stream().filter(issue -> "LLM_CONFIG_INVALID".equals(issue.code())).count() >= 2);
    }

    @Test
    void shouldAcceptRetrievalAndHttpRuntimeNodesWithValidConfig() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("search", "retrieval.search", Map.of("kb_id", "kb-1", "top_k", 5)),
                        new CanvasModels.Node("tool", "tool.http", Map.of(
                                "url", "https://api.example.com/v1",
                                "method", "POST",
                                "auth", Map.of("secret_ref", "billing-api", "header", "Authorization", "prefix", "Bearer "),
                                "retry", Map.of("max_attempts", 3, "initial_delay_ms", 100, "backoff_multiplier", 2, "max_delay_ms", 1000, "retry_on", "transient")
                        )),
                        new CanvasModels.Node("llm", "llm.generate", Map.of()),
                        new CanvasModels.Node("end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "search", null),
                        new CanvasModels.Edge("e2", "search", "tool", null),
                        new CanvasModels.Edge("e3", "tool", "llm", null),
                        new CanvasModels.Edge("e4", "llm", "end", null)
                )
        );

        assertTrue(dslValidator.validate(dsl).valid());
    }

    @Test
    void shouldRejectInvalidRetrievalAndHttpConfig() {
        CanvasModels.AgentCanvasDsl dsl = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("search", "retrieval.search", Map.of("kb_id", "", "top_k", 100)),
                        new CanvasModels.Node("tool", "tool.http", Map.of(
                                "url", "ftp://user:pass@example.com/file#secret",
                                "method", "TRACE",
                                "timeout_seconds", 0,
                                "headers", Map.of("Authorization", "secret"),
                                "auth", Map.of("secret_ref", "", "header", "Cookie", "prefix", "bad\n"),
                                "retry", Map.of("max_attempts", 2.5, "initial_delay_ms", 5000, "max_delay_ms", 100, "retry_on", "forever", "typo", true)
                        )),
                        new CanvasModels.Node("llm", "llm.generate", Map.of()),
                        new CanvasModels.Node("end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "search", null),
                        new CanvasModels.Edge("e2", "search", "tool", null),
                        new CanvasModels.Edge("e3", "tool", "llm", null),
                        new CanvasModels.Edge("e4", "llm", "end", null)
                )
        );

        CanvasModels.ValidationResult result = dslValidator.validate(dsl);
        assertTrue(result.errors().stream().anyMatch(issue -> "RETRIEVAL_CONFIG_INVALID".equals(issue.code())));
        assertTrue(result.errors().stream().anyMatch(issue -> "HTTP_TOOL_CONFIG_INVALID".equals(issue.code())));
        assertTrue(result.errors().stream().anyMatch(issue -> "NODE_RETRY_CONFIG_INVALID".equals(issue.code())));
    }

    @Test
    void shouldAcceptRag2RetrievalAndRejectInvalidRerankConfig() {
        CanvasModels.AgentCanvasDsl valid = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("search", "retrieval.search", Map.of(
                                "kb_id", "kb-1", "top_k", 6, "candidate_k", 30,
                                "rank_method", "hybrid", "max_chunks_per_doc", 3,
                                "context_window", 1, "rerank_enabled", true,
                                "rerank_k", 30, "rerank_required", false
                        )),
                        new CanvasModels.Node("llm", "llm.generate", Map.of()),
                        new CanvasModels.Node("end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "search", null),
                        new CanvasModels.Edge("e2", "search", "llm", null),
                        new CanvasModels.Edge("e3", "llm", "end", null)
                )
        );
        assertTrue(dslValidator.validate(valid).valid());

        CanvasModels.AgentCanvasDsl invalid = new CanvasModels.AgentCanvasDsl(
                "start",
                List.of(
                        new CanvasModels.Node("start", "start", Map.of()),
                        new CanvasModels.Node("search", "retrieval.search", Map.of(
                                "kb_id", "kb-1", "top_k", 6, "rank_method", "fake",
                                "rerank_enabled", "yes", "rerank_k", 101
                        )),
                        new CanvasModels.Node("llm", "llm.generate", Map.of()),
                        new CanvasModels.Node("end", "end", Map.of())
                ),
                List.of(
                        new CanvasModels.Edge("e1", "start", "search", null),
                        new CanvasModels.Edge("e2", "search", "llm", null),
                        new CanvasModels.Edge("e3", "llm", "end", null)
                )
        );
        assertFalse(dslValidator.validate(invalid).valid());
    }
}
