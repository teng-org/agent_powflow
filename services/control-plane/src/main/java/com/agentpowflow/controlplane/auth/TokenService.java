package com.agentpowflow.controlplane.auth;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

@Service
public class TokenService {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final Pattern KEY_ID_PATTERN = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$");
    private static final Pattern BASE64_URL_PATTERN = Pattern.compile("^[A-Za-z0-9_-]+$");
    private static final int MAX_TOKEN_LENGTH = 8192;
    private static final int MAX_PREVIOUS_KEYS = 3;
    private static final long CLOCK_SKEW_SECONDS = 60L;

    private final AuthProperties authProperties;
    private final KeyRing keyRing;

    public TokenService(AuthProperties authProperties) {
        this.authProperties = authProperties;
        this.keyRing = buildKeyRing(authProperties);
    }

    public String signAccessToken(String subject, String tenantId, List<String> roles) {
        long now = Instant.now().getEpochSecond();
        Map<String, Object> payload = basePayload(subject, tenantId, roles, "access", now,
                now + Math.max(60L, authProperties.getAccessTtlSeconds()));
        return signPayload(payload);
    }

    public String signRefreshToken(String subject, String tenantId, List<String> roles) {
        long now = Instant.now().getEpochSecond();
        Map<String, Object> payload = basePayload(subject, tenantId, roles, "refresh", now,
                now + Math.max(600L, authProperties.getRefreshTtlSeconds()));
        return signPayload(payload);
    }

    public TokenClaims verify(String token, String expectedTokenType) {
        return verifyAt(token, expectedTokenType, Instant.now());
    }

    TokenClaims verifyAt(String token, String expectedTokenType, Instant nowInstant) {
        if (token == null || token.isBlank() || token.length() > MAX_TOKEN_LENGTH) {
            throw unauthorized("invalid token format");
        }
        String[] parts = token.split("\\.", -1);
        boolean legacy;
        Map<String, Object> payload;
        String keyId;
        String keySlot;
        String signingInput;
        String signaturePart;

        if (parts.length == 3) {
            legacy = false;
            Map<String, Object> header = decodeObject(parts[0], "header");
            if (!"HS256".equals(header.get("alg")) || !"JWT".equals(header.get("typ"))) {
                throw unauthorized("unsupported token header");
            }
            keyId = String.valueOf(header.getOrDefault("kid", ""));
            VerificationKey key = selectKey(keyId, nowInstant);
            keySlot = key.slot();
            signingInput = parts[0] + "." + parts[1];
            signaturePart = parts[2];
            if (!signatureMatches(signingInput, signaturePart, key.secret())) {
                throw unauthorized("invalid token signature");
            }
            payload = decodeObject(parts[1], "payload");
            if (!keyRing.issuer().equals(payload.get("iss")) || !keyRing.audience().equals(payload.get("aud"))) {
                throw unauthorized("token issuer or audience mismatch");
            }
        } else if (parts.length == 2) {
            if (keyRing.legacyAcceptUntil() == null || !nowInstant.isBefore(keyRing.legacyAcceptUntil())) {
                throw unauthorized("legacy token format expired");
            }
            legacy = true;
            signingInput = parts[0];
            signaturePart = parts[1];
            payload = decodeObject(parts[0], "payload");
            VerificationKey matched = null;
            for (VerificationKey candidate : activeKeys(nowInstant)) {
                if (signatureMatches(signingInput, signaturePart, candidate.secret())) {
                    matched = candidate;
                    break;
                }
            }
            if (matched == null) {
                throw unauthorized("invalid token signature");
            }
            keyId = matched.id();
            keySlot = matched.slot();
        } else {
            throw unauthorized("invalid token format");
        }

        long exp = parseLong(payload.get("exp"), 0L);
        long iat = parseLong(payload.get("iat"), 0L);
        long now = nowInstant.getEpochSecond();
        if (exp <= now) {
            throw unauthorized("token expired");
        }
        if (iat <= 0L || iat > now + CLOCK_SKEW_SECONDS || exp <= iat) {
            throw unauthorized("invalid token timestamps");
        }
        String tokenType = String.valueOf(payload.getOrDefault("token_type", "access"));
        if (expectedTokenType != null && !expectedTokenType.isBlank() && !expectedTokenType.equals(tokenType)) {
            throw unauthorized("token type mismatch");
        }
        String subject = String.valueOf(payload.getOrDefault("sub", ""));
        String tenantId = String.valueOf(payload.getOrDefault("tenant_id", authProperties.getAdminTenantId()));
        List<String> roles = parseRoles(payload);
        if (subject.isBlank()) {
            throw unauthorized("token subject missing");
        }
        return new TokenClaims(subject, tenantId, roles, tokenType, iat, exp, keyId, keySlot, legacy);
    }

    private Map<String, Object> basePayload(
            String subject, String tenantId, List<String> roles, String tokenType, long iat, long exp
    ) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("sub", subject);
        payload.put("tenant_id", tenantId);
        payload.put("roles", roles);
        payload.put("token_type", tokenType);
        payload.put("jti", UUID.randomUUID().toString());
        payload.put("iat", iat);
        payload.put("exp", exp);
        return payload;
    }

    private String signPayload(Map<String, Object> originalPayload) {
        try {
            Map<String, Object> header = Map.of("alg", "HS256", "typ", "JWT", "kid", keyRing.primary().id());
            Map<String, Object> payload = new HashMap<>(originalPayload);
            payload.put("iss", keyRing.issuer());
            payload.put("aud", keyRing.audience());
            String headerPart = base64UrlEncode(OBJECT_MAPPER.writeValueAsBytes(header));
            String payloadPart = base64UrlEncode(OBJECT_MAPPER.writeValueAsBytes(payload));
            String signingInput = headerPart + "." + payloadPart;
            return signingInput + "." + base64UrlEncode(signHmac(signingInput, keyRing.primary().secret()));
        } catch (Exception ex) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, "token sign failed");
        }
    }

    private VerificationKey selectKey(String keyId, Instant now) {
        if (keyRing.primary().id().equals(keyId)) {
            return keyRing.primary();
        }
        if (previousActive(now)) {
            int index = 1;
            for (Map.Entry<String, String> entry : keyRing.previous().entrySet()) {
                if (entry.getKey().equals(keyId)) {
                    return new VerificationKey(entry.getKey(), entry.getValue(), "previous:" + index);
                }
                index++;
            }
        }
        throw unauthorized("unknown or expired token key id");
    }

    private List<VerificationKey> activeKeys(Instant now) {
        List<VerificationKey> result = new ArrayList<>();
        result.add(keyRing.primary());
        if (previousActive(now)) {
            int index = 1;
            for (Map.Entry<String, String> entry : keyRing.previous().entrySet()) {
                result.add(new VerificationKey(entry.getKey(), entry.getValue(), "previous:" + index));
                index++;
            }
        }
        return result;
    }

    private boolean previousActive(Instant now) {
        return !keyRing.previous().isEmpty() && keyRing.previousExpiresAt() != null && now.isBefore(keyRing.previousExpiresAt());
    }

    private static KeyRing buildKeyRing(AuthProperties properties) {
        String primaryId = String.valueOf(properties.getJwtKeyId()).trim();
        String primarySecret = String.valueOf(properties.getJwtSecret()).trim();
        if (!KEY_ID_PATTERN.matcher(primaryId).matches() || primarySecret.isEmpty()) {
            throw new IllegalStateException("JWT primary key configuration is invalid");
        }
        LinkedHashMap<String, String> previous = new LinkedHashMap<>();
        String rawPrevious = String.valueOf(properties.getJwtPreviousKeys() == null ? "" : properties.getJwtPreviousKeys()).trim();
        if (!rawPrevious.isEmpty()) {
            for (String rawItem : rawPrevious.split(",")) {
                String item = rawItem.trim();
                if (item.isEmpty()) {
                    continue;
                }
                int separator = item.indexOf(':');
                if (separator < 1 || separator == item.length() - 1) {
                    throw new IllegalStateException("JWT previous keys must use kid:secret entries");
                }
                String keyId = item.substring(0, separator).trim();
                String secret = item.substring(separator + 1).trim();
                if (!KEY_ID_PATTERN.matcher(keyId).matches() || secret.isEmpty() || previous.putIfAbsent(keyId, secret) != null) {
                    throw new IllegalStateException("JWT previous key entry is invalid or duplicated");
                }
            }
        }
        if (previous.size() > MAX_PREVIOUS_KEYS || previous.containsKey(primaryId)) {
            throw new IllegalStateException("JWT primary and previous key ids are invalid");
        }
        List<String> secrets = new ArrayList<>();
        secrets.add(primarySecret);
        secrets.addAll(previous.values());
        if (Set.copyOf(secrets).size() != secrets.size()) {
            throw new IllegalStateException("JWT primary and previous secrets must be distinct");
        }
        String rawExpiry = String.valueOf(
                properties.getJwtPreviousKeysExpiresAt() == null ? "" : properties.getJwtPreviousKeysExpiresAt()
        ).trim();
        Instant expiry = null;
        if (!rawExpiry.isEmpty()) {
            try {
                expiry = Instant.parse(rawExpiry);
            } catch (DateTimeParseException ex) {
                throw new IllegalStateException("JWT previous key expiry must be ISO-8601 UTC", ex);
            }
        }
        if (!previous.isEmpty() && expiry == null) {
            throw new IllegalStateException("JWT previous keys require an expiry");
        }
        if (previous.isEmpty() && expiry != null) {
            throw new IllegalStateException("JWT previous key expiry requires previous keys");
        }
        Instant startupNow = Instant.now();
        if (expiry != null && (!expiry.isAfter(startupNow) || expiry.isAfter(startupNow.plusSeconds(604800)))) {
            throw new IllegalStateException("JWT previous key expiry must be in the future and at most 7 days away");
        }
        String rawLegacyExpiry = String.valueOf(
                properties.getJwtLegacyAcceptUntil() == null ? "" : properties.getJwtLegacyAcceptUntil()
        ).trim();
        Instant legacyExpiry = null;
        if (!rawLegacyExpiry.isEmpty()) {
            try {
                legacyExpiry = Instant.parse(rawLegacyExpiry);
            } catch (DateTimeParseException ex) {
                throw new IllegalStateException("JWT legacy acceptance deadline must be ISO-8601 UTC", ex);
            }
            if (!legacyExpiry.isAfter(startupNow) || legacyExpiry.isAfter(startupNow.plusSeconds(604800))) {
                throw new IllegalStateException("JWT legacy acceptance deadline must be in the future and at most 7 days away");
            }
        }
        String issuer = String.valueOf(properties.getJwtIssuer()).trim();
        String audience = String.valueOf(properties.getJwtAudience()).trim();
        if (issuer.isEmpty() || audience.isEmpty()) {
            throw new IllegalStateException("JWT issuer and audience are required");
        }
        return new KeyRing(
                new VerificationKey(primaryId, primarySecret, "primary"), previous, expiry, legacyExpiry, issuer, audience
        );
    }

    private static Map<String, Object> decodeObject(String encoded, String label) {
        try {
            String payloadJson = new String(base64UrlDecode(encoded), StandardCharsets.UTF_8);
            Map<String, Object> value = OBJECT_MAPPER.readValue(payloadJson, new TypeReference<>() {
            });
            if (value == null) {
                throw new IllegalArgumentException("null object");
            }
            return value;
        } catch (Exception ex) {
            throw unauthorized("invalid token " + label);
        }
    }

    private static boolean signatureMatches(String signingInput, String signaturePart, String secret) {
        try {
            return MessageDigest.isEqual(signHmac(signingInput, secret), base64UrlDecode(signaturePart));
        } catch (Exception ex) {
            return false;
        }
    }

    private static byte[] signHmac(String signingInput, String secret) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return mac.doFinal(signingInput.getBytes(StandardCharsets.US_ASCII));
    }

    private static long parseLong(Object value, long fallback) {
        try {
            return value instanceof Number number ? number.longValue() : Long.parseLong(String.valueOf(value));
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
        return single.isEmpty() ? List.of() : List.of(single);
    }

    private static String base64UrlEncode(byte[] raw) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    }

    private static byte[] base64UrlDecode(String raw) {
        if (raw == null || !BASE64_URL_PATTERN.matcher(raw).matches()) {
            throw new IllegalArgumentException("invalid base64url");
        }
        int rem = raw.length() % 4;
        String padded = rem == 0 ? raw : (raw + "====".substring(rem));
        return Base64.getUrlDecoder().decode(padded);
    }

    private static ResponseStatusException unauthorized(String reason) {
        return new ResponseStatusException(HttpStatus.UNAUTHORIZED, reason);
    }

    private record VerificationKey(String id, String secret, String slot) {
    }

    private record KeyRing(
            VerificationKey primary,
            LinkedHashMap<String, String> previous,
            Instant previousExpiresAt,
            Instant legacyAcceptUntil,
            String issuer,
            String audience
    ) {
    }
}
