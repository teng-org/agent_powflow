package com.agentpowflow.controlplane.auth;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

@Service
public class TokenService {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private final AuthProperties authProperties;

    public TokenService(AuthProperties authProperties) {
        this.authProperties = authProperties;
    }

    public String signAccessToken(String subject, String tenantId, List<String> roles) {
        long now = Instant.now().getEpochSecond();
        Map<String, Object> payload = new HashMap<>();
        payload.put("sub", subject);
        payload.put("tenant_id", tenantId);
        payload.put("roles", roles);
        payload.put("token_type", "access");
        payload.put("iat", now);
        payload.put("exp", now + Math.max(60L, authProperties.getAccessTtlSeconds()));
        return signPayload(payload);
    }

    public String signRefreshToken(String subject, String tenantId, List<String> roles) {
        long now = Instant.now().getEpochSecond();
        Map<String, Object> payload = new HashMap<>();
        payload.put("sub", subject);
        payload.put("tenant_id", tenantId);
        payload.put("roles", roles);
        payload.put("token_type", "refresh");
        payload.put("iat", now);
        payload.put("exp", now + Math.max(600L, authProperties.getRefreshTtlSeconds()));
        return signPayload(payload);
    }

    public TokenClaims verify(String token, String expectedTokenType) {
        String[] parts = token.split("\\.");
        if (parts.length != 2) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid token format");
        }
        String payloadPart = parts[0];
        String signaturePart = parts[1];
        String expectedSig = signHmac(payloadPart);
        if (!Objects.equals(expectedSig, signaturePart)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid token signature");
        }

        Map<String, Object> payload;
        try {
            String payloadJson = new String(base64UrlDecode(payloadPart), StandardCharsets.UTF_8);
            payload = OBJECT_MAPPER.readValue(payloadJson, new TypeReference<>() {
            });
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "invalid token payload");
        }

        long exp = parseLong(payload.get("exp"), 0L);
        long iat = parseLong(payload.get("iat"), 0L);
        long now = Instant.now().getEpochSecond();
        if (exp <= now) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "token expired");
        }
        String tokenType = String.valueOf(payload.getOrDefault("token_type", "access"));
        if (expectedTokenType != null && !expectedTokenType.isBlank() && !expectedTokenType.equals(tokenType)) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "token type mismatch");
        }

        String subject = String.valueOf(payload.getOrDefault("sub", ""));
        String tenantId = String.valueOf(payload.getOrDefault("tenant_id", authProperties.getAdminTenantId()));
        List<String> roles = parseRoles(payload);
        if (subject.isBlank()) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "token subject missing");
        }
        return new TokenClaims(subject, tenantId, roles, tokenType, iat, exp);
    }

    private String signPayload(Map<String, Object> payload) {
        try {
            String payloadJson = OBJECT_MAPPER.writeValueAsString(payload);
            String payloadPart = base64UrlEncode(payloadJson.getBytes(StandardCharsets.UTF_8));
            String sig = signHmac(payloadPart);
            return payloadPart + "." + sig;
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "token sign failed");
        }
    }

    private String signHmac(String payloadPart) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            SecretKeySpec keySpec = new SecretKeySpec(
                    authProperties.getJwtSecret().getBytes(StandardCharsets.UTF_8),
                    "HmacSHA256"
            );
            mac.init(keySpec);
            byte[] digest = mac.doFinal(payloadPart.getBytes(StandardCharsets.UTF_8));
            return base64UrlEncode(digest);
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "token sign failed");
        }
    }

    private static long parseLong(Object value, long fallback) {
        try {
            if (value instanceof Number number) {
                return number.longValue();
            }
            return Long.parseLong(String.valueOf(value));
        } catch (Exception ex) {
            return fallback;
        }
    }

    private static List<String> parseRoles(Map<String, Object> payload) {
        Object rolesVal = payload.get("roles");
        if (rolesVal instanceof List<?> list) {
            List<String> roles = new ArrayList<>();
            for (Object item : list) {
                String role = String.valueOf(item).trim();
                if (!role.isEmpty()) {
                    roles.add(role);
                }
            }
            if (!roles.isEmpty()) {
                return roles;
            }
        }
        String single = String.valueOf(payload.getOrDefault("role", "")).trim();
        if (!single.isEmpty()) {
            return List.of(single);
        }
        return List.of();
    }

    private static String base64UrlEncode(byte[] raw) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    }

    private static byte[] base64UrlDecode(String raw) {
        int rem = raw.length() % 4;
        String padded = rem == 0 ? raw : (raw + "====".substring(rem));
        return Base64.getUrlDecoder().decode(padded);
    }
}

