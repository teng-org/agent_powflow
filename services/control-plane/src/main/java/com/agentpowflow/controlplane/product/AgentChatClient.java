package com.agentpowflow.controlplane.product;

import com.agentpowflow.controlplane.runs.InternalApiProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.server.ResponseStatusException;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;

import static org.springframework.http.HttpStatus.BAD_GATEWAY;

@Component
public class AgentChatClient {
    private final InternalApiProperties properties;
    private final RestClient restClient;

    public AgentChatClient(InternalApiProperties properties) {
        this.properties = properties;
        Duration timeout = Duration.ofMillis(Math.max(1000, properties.getChatRequestTimeoutMs()));
        HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(client);
        factory.setReadTimeout(timeout);
        this.restClient = RestClient.builder().requestFactory(factory).build();
    }

    public AgentResult invoke(
            String tenantId,
            String actorId,
            JsonNode snapshot,
            JsonNode input,
            String conversationId
    ) {
        try {
            JsonNode response = restClient.post()
                    .uri(properties.getChatBaseUrl() + "/api/v1/internal/agents/invoke")
                    .header("X-Internal-Token", properties.getToken())
                    .body(Map.of(
                            "tenant_id", tenantId,
                            "actor_id", actorId,
                            "agent_snapshot", snapshot,
                            "input", input,
                            "conversation_id", conversationId == null ? "" : conversationId
                    ))
                    .retrieve()
                    .body(JsonNode.class);
            if (response == null || response.path("run_id").asText("").isBlank()) {
                throw new ResponseStatusException(BAD_GATEWAY, "agent runtime returned an invalid response");
            }
            return new AgentResult(
                    response.path("run_id").asText(),
                    response.path("conversation_id").asText(""),
                    response.path("output").asText(""),
                    response.path("citations").isArray()
                            ? response.path("citations") : JsonNodeFactory.instance.arrayNode(),
                    response.path("metrics").isObject()
                            ? response.path("metrics") : JsonNodeFactory.instance.objectNode()
            );
        } catch (RestClientResponseException error) {
            throw new ResponseStatusException(BAD_GATEWAY, "agent runtime is temporarily unavailable", error);
        } catch (RestClientException error) {
            throw new ResponseStatusException(BAD_GATEWAY, "agent runtime is temporarily unavailable", error);
        }
    }

    public record AgentResult(
            String runId,
            String conversationId,
            String output,
            JsonNode citations,
            JsonNode metrics
    ) {}
}
