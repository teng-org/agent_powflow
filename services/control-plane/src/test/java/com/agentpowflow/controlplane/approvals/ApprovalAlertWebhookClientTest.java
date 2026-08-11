package com.agentpowflow.controlplane.approvals;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ApprovalAlertWebhookClientTest {
    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) server.stop(0);
    }

    @Test
    void rejectsUnallowlistedAndPrivateDestinations() {
        ApprovalAlertProperties properties = new ApprovalAlertProperties();
        properties.setWebhookUrl("https://127.0.0.1/hook");
        ApprovalAlertWebhookClient client = new ApprovalAlertWebhookClient(properties);

        ApprovalAlertWebhookClient.DeliveryException notAllowlisted = assertThrows(
                ApprovalAlertWebhookClient.DeliveryException.class, client::validateDestination
        );
        assertFalse(notAllowlisted.isRetryable());
        assertTrue(notAllowlisted.getMessage().contains("allowlisted"));

        properties.setAllowedHosts("127.0.0.1");
        ApprovalAlertWebhookClient.DeliveryException privateAddress = assertThrows(
                ApprovalAlertWebhookClient.DeliveryException.class, client::validateDestination
        );
        assertTrue(privateAddress.getMessage().contains("private"));
    }

    @Test
    void rejectsPlainHttpUnlessExplicitlyEnabled() {
        ApprovalAlertProperties properties = new ApprovalAlertProperties();
        properties.setWebhookUrl("http://example.com/hook");
        properties.setAllowedHosts("example.com");
        ApprovalAlertWebhookClient client = new ApprovalAlertWebhookClient(properties);

        ApprovalAlertWebhookClient.DeliveryException error = assertThrows(
                ApprovalAlertWebhookClient.DeliveryException.class, client::validateDestination
        );

        assertTrue(error.getMessage().contains("scheme"));
    }

    @Test
    void postsStableIdempotencyHeadersAndPayloadToExplicitLocalTestTarget() throws Exception {
        AtomicReference<String> body = new AtomicReference<>();
        AtomicReference<String> eventId = new AtomicReference<>();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/hook", exchange -> {
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            eventId.set(exchange.getRequestHeaders().getFirst("X-Agent-Powflow-Event-Id"));
            exchange.sendResponseHeaders(204, -1);
            exchange.close();
        });
        server.start();

        ApprovalAlertProperties properties = new ApprovalAlertProperties();
        properties.setWebhookUrl("http://127.0.0.1:" + server.getAddress().getPort() + "/hook");
        properties.setAllowedHosts("127.0.0.1");
        properties.setAllowHttp(true);
        properties.setAllowPrivateNetwork(true);
        ApprovalAlertDeliveryEntity delivery = new ApprovalAlertDeliveryEntity();
        delivery.setDeliveryId("delivery-1");
        delivery.setAlertId("alert-1");
        delivery.setPayloadJson("{\"event_id\":\"alert-1\"}");

        new ApprovalAlertWebhookClient(properties).send(delivery);

        assertEquals("{\"event_id\":\"alert-1\"}", body.get());
        assertEquals("alert-1", eventId.get());
    }

    @Test
    void classifiesRateLimitAsRetryable() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/hook", exchange -> {
            exchange.sendResponseHeaders(429, -1);
            exchange.close();
        });
        server.start();
        ApprovalAlertProperties properties = new ApprovalAlertProperties();
        properties.setWebhookUrl("http://127.0.0.1:" + server.getAddress().getPort() + "/hook");
        properties.setAllowedHosts("127.0.0.1");
        properties.setAllowHttp(true);
        properties.setAllowPrivateNetwork(true);
        ApprovalAlertDeliveryEntity delivery = new ApprovalAlertDeliveryEntity();
        delivery.setDeliveryId("delivery-1");
        delivery.setAlertId("alert-1");
        delivery.setPayloadJson("{}");

        ApprovalAlertWebhookClient.DeliveryException error = assertThrows(
                ApprovalAlertWebhookClient.DeliveryException.class,
                () -> new ApprovalAlertWebhookClient(properties).send(delivery)
        );

        assertTrue(error.isRetryable());
    }
}
