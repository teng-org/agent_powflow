package com.agentpowflow.controlplane.approvals;

import org.springframework.stereotype.Component;

import java.io.IOException;
import java.net.Inet4Address;
import java.net.Inet6Address;
import java.net.InetAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Arrays;
import java.util.Locale;
import java.util.Set;
import java.util.stream.Collectors;

@Component
public class ApprovalAlertWebhookClient {
    private static final Set<Integer> RETRYABLE_STATUSES = Set.of(408, 425, 429, 500, 502, 503, 504);
    private final ApprovalAlertProperties properties;
    private final HttpClient client;

    public ApprovalAlertWebhookClient(ApprovalAlertProperties properties) {
        this.properties = properties;
        this.client = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .connectTimeout(Duration.ofMillis(Math.max(500, properties.getRequestTimeoutMs())))
                .build();
    }

    public void send(ApprovalAlertDeliveryEntity delivery) {
        URI uri = validateDestination();
        HttpRequest request = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofMillis(Math.max(500, properties.getRequestTimeoutMs())))
                .header("Content-Type", "application/json")
                .header("User-Agent", "agent-powflow-control-plane/1")
                .header("X-Agent-Powflow-Event-Id", delivery.getAlertId())
                .header("X-Agent-Powflow-Delivery-Id", delivery.getDeliveryId())
                .POST(HttpRequest.BodyPublishers.ofString(delivery.getPayloadJson()))
                .build();
        try {
            HttpResponse<Void> response = client.send(request, HttpResponse.BodyHandlers.discarding());
            int status = response.statusCode();
            if (status >= 200 && status < 300) return;
            throw new DeliveryException("approval alert webhook returned HTTP " + status, RETRYABLE_STATUSES.contains(status));
        } catch (DeliveryException ex) {
            throw ex;
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new DeliveryException("approval alert webhook was interrupted", false, ex);
        } catch (IOException ex) {
            throw new DeliveryException("approval alert webhook network failure", true, ex);
        }
    }

    URI validateDestination() {
        if (properties.getWebhookUrl().isBlank()) {
            throw new DeliveryException("approval alert webhook is not configured", false);
        }
        final URI uri;
        try { uri = URI.create(properties.getWebhookUrl()); }
        catch (IllegalArgumentException ex) { throw new DeliveryException("approval alert webhook URL is invalid", false, ex); }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!"https".equals(scheme) && !(properties.isAllowHttp() && "http".equals(scheme))) {
            throw new DeliveryException("approval alert webhook scheme is not allowed", false);
        }
        if (uri.getHost() == null || uri.getHost().isBlank() || uri.getUserInfo() != null || uri.getFragment() != null) {
            throw new DeliveryException("approval alert webhook authority is invalid", false);
        }
        String host = uri.getHost().toLowerCase(Locale.ROOT);
        Set<String> allowedHosts = Arrays.stream(properties.getAllowedHosts().split(","))
                .map(String::trim).map(value -> value.toLowerCase(Locale.ROOT))
                .filter(value -> !value.isBlank()).collect(Collectors.toSet());
        if (allowedHosts.isEmpty() || !allowedHosts.contains(host)) {
            throw new DeliveryException("approval alert webhook host is not allowlisted", false);
        }
        try {
            for (InetAddress address : InetAddress.getAllByName(host)) {
                if (!properties.isAllowPrivateNetwork() && isForbidden(address)) {
                    throw new DeliveryException("approval alert webhook resolved to a private or special-use address", false);
                }
            }
        } catch (DeliveryException ex) {
            throw ex;
        } catch (IOException ex) {
            throw new DeliveryException("approval alert webhook host could not be resolved", true, ex);
        }
        return uri;
    }

    private boolean isForbidden(InetAddress address) {
        if (address.isAnyLocalAddress() || address.isLoopbackAddress() || address.isLinkLocalAddress()
                || address.isSiteLocalAddress() || address.isMulticastAddress()) return true;
        byte[] bytes = address.getAddress();
        if (address instanceof Inet4Address && bytes.length == 4) {
            int first = Byte.toUnsignedInt(bytes[0]);
            int second = Byte.toUnsignedInt(bytes[1]);
            return first == 0 || first == 10 || first == 127
                    || (first == 100 && second >= 64 && second <= 127)
                    || (first == 169 && second == 254)
                    || (first == 172 && second >= 16 && second <= 31)
                    || (first == 192 && second == 168)
                    || first >= 224;
        }
        return address instanceof Inet6Address && bytes.length == 16 && (Byte.toUnsignedInt(bytes[0]) & 0xfe) == 0xfc;
    }

    public static class DeliveryException extends RuntimeException {
        private final boolean retryable;
        public DeliveryException(String message, boolean retryable) { super(message); this.retryable = retryable; }
        public DeliveryException(String message, boolean retryable, Throwable cause) { super(message, cause); this.retryable = retryable; }
        public boolean isRetryable() { return retryable; }
    }
}
