package com.agentpowflow.controlplane.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TokenServiceTest {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final String PRIMARY = "primary-value-with-at-least-32-characters";
    private static final String PREVIOUS = "previous-value-with-at-least-32-characters";

    @Test
    void newTokensAreStandardHs256AndExposePrimaryKeySlot() throws Exception {
        TokenService service = serviceWithPrevious(Instant.now().plusSeconds(3600));
        String token = service.signAccessToken("admin", "platform", List.of("PLATFORM_ADMIN"));
        assertEquals(3, token.split("\\.").length);
        String header = new String(decode(token.split("\\.")[0]), StandardCharsets.UTF_8);
        assertTrue(header.contains("\"alg\":\"HS256\""));
        assertTrue(header.contains("\"kid\":\"key-current\""));

        TokenClaims claims = service.verify(token, "access");
        assertEquals("key-current", claims.keyId());
        assertEquals("primary", claims.keySlot());
        assertFalse(claims.legacyFormat());
        assertEquals(List.of("PLATFORM_ADMIN"), claims.roles());
    }

    @Test
    void legacyPrimaryAndPreviousTokensWorkOnlyInsideCompatibilityWindow() throws Exception {
        Instant now = Instant.now();
        TokenService service = serviceWithPrevious(now.plusSeconds(3600));
        Map<String, Object> payload = payload(now.getEpochSecond(), now.plusSeconds(7200).getEpochSecond(), "access");

        TokenClaims primary = service.verifyAt(legacyToken(payload, PRIMARY), "access", now);
        TokenClaims previous = service.verifyAt(legacyToken(payload, PREVIOUS), "access", now);
        assertEquals("primary", primary.keySlot());
        assertEquals("previous:1", previous.keySlot());
        assertTrue(previous.legacyFormat());

        ResponseStatusException expiredWindow = assertThrows(
                ResponseStatusException.class,
                () -> service.verifyAt(legacyToken(payload, PREVIOUS), "access", now.plusSeconds(3601))
        );
        assertEquals("legacy token format expired", expiredWindow.getReason());
    }

    @Test
    void legacyFormatIsDisabledWithoutExplicitDeadline() throws Exception {
        Instant now = Instant.now();
        TokenService service = new TokenService(baseProperties());
        Map<String, Object> payload = payload(now.getEpochSecond(), now.plusSeconds(600).getEpochSecond(), "access");
        ResponseStatusException rejected = assertThrows(
                ResponseStatusException.class,
                () -> service.verifyAt(legacyToken(payload, PRIMARY), "access", now)
        );
        assertEquals("legacy token format expired", rejected.getReason());
    }

    @Test
    void previousStandardKidIsAcceptedButUnknownKidNeverFallsBack() throws Exception {
        Instant now = Instant.now();
        TokenService service = serviceWithPrevious(now.plusSeconds(3600));
        Map<String, Object> payload = payload(now.getEpochSecond(), now.plusSeconds(600).getEpochSecond(), "access");
        payload.put("iss", "agentpowflow-control");
        payload.put("aud", "control-plane");
        TokenClaims previous = service.verifyAt(standardToken(payload, "key-old", PREVIOUS), "access", now);
        assertEquals("previous:1", previous.keySlot());

        ResponseStatusException unknown = assertThrows(
                ResponseStatusException.class,
                () -> service.verifyAt(standardToken(payload, "key-unknown", PRIMARY), "access", now)
        );
        assertEquals("unknown or expired token key id", unknown.getReason());
    }

    @Test
    void issuerAudienceTokenTypeAndTimestampsAreEnforced() throws Exception {
        Instant now = Instant.now();
        TokenService service = serviceWithPrevious(now.plusSeconds(3600));
        Map<String, Object> wrongAudience = payload(now.getEpochSecond(), now.plusSeconds(600).getEpochSecond(), "access");
        wrongAudience.put("iss", "agentpowflow-control");
        wrongAudience.put("aud", "execution-plane");
        assertEquals(
                "token issuer or audience mismatch",
                assertThrows(ResponseStatusException.class,
                        () -> service.verifyAt(standardToken(wrongAudience, "key-current", PRIMARY), "access", now)).getReason()
        );

        String refresh = service.signRefreshToken("admin", "platform", List.of("PLATFORM_ADMIN"));
        assertEquals("refresh", service.verify(refresh, "refresh").tokenType());
        assertEquals("token type mismatch", assertThrows(ResponseStatusException.class,
                () -> service.verify(refresh, "access")).getReason());

        Map<String, Object> future = payload(now.plusSeconds(61).getEpochSecond(), now.plusSeconds(600).getEpochSecond(), "access");
        assertEquals("invalid token timestamps", assertThrows(ResponseStatusException.class,
                () -> service.verifyAt(legacyToken(future, PRIMARY), "access", now)).getReason());
    }

    @Test
    void previousConfigurationRequiresDistinctKeysAndFutureExpiry() {
        AuthProperties missingExpiry = baseProperties();
        missingExpiry.setJwtPreviousKeys("key-old:" + PREVIOUS);
        assertThrows(IllegalStateException.class, () -> new TokenService(missingExpiry));

        AuthProperties reused = baseProperties();
        reused.setJwtPreviousKeys("key-old:" + PRIMARY);
        reused.setJwtPreviousKeysExpiresAt(Instant.now().plusSeconds(3600).toString());
        assertThrows(IllegalStateException.class, () -> new TokenService(reused));

        AuthProperties stale = baseProperties();
        stale.setJwtPreviousKeys("key-old:" + PREVIOUS);
        stale.setJwtPreviousKeysExpiresAt(Instant.now().minusSeconds(1).toString());
        assertThrows(IllegalStateException.class, () -> new TokenService(stale));

        AuthProperties tooLong = baseProperties();
        tooLong.setJwtLegacyAcceptUntil(Instant.now().plusSeconds(604900).toString());
        assertThrows(IllegalStateException.class, () -> new TokenService(tooLong));
    }

    private static TokenService serviceWithPrevious(Instant expiry) {
        AuthProperties properties = baseProperties();
        properties.setJwtPreviousKeys("key-old:" + PREVIOUS);
        properties.setJwtPreviousKeysExpiresAt(expiry.toString());
        properties.setJwtLegacyAcceptUntil(expiry.toString());
        return new TokenService(properties);
    }

    private static AuthProperties baseProperties() {
        AuthProperties properties = new AuthProperties();
        properties.setJwtSecret(PRIMARY);
        properties.setJwtKeyId("key-current");
        properties.setJwtIssuer("agentpowflow-control");
        properties.setJwtAudience("control-plane");
        return properties;
    }

    private static Map<String, Object> payload(long iat, long exp, String type) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("sub", "admin");
        payload.put("tenant_id", "platform");
        payload.put("roles", List.of("PLATFORM_ADMIN"));
        payload.put("token_type", type);
        payload.put("iat", iat);
        payload.put("exp", exp);
        return payload;
    }

    private static String legacyToken(Map<String, Object> payload, String secret) throws Exception {
        String payloadPart = encode(OBJECT_MAPPER.writeValueAsBytes(payload));
        return payloadPart + "." + signature(payloadPart, secret);
    }

    private static String standardToken(Map<String, Object> payload, String keyId, String secret) throws Exception {
        String headerPart = encode(OBJECT_MAPPER.writeValueAsBytes(Map.of("alg", "HS256", "typ", "JWT", "kid", keyId)));
        String payloadPart = encode(OBJECT_MAPPER.writeValueAsBytes(payload));
        String signingInput = headerPart + "." + payloadPart;
        return signingInput + "." + signature(signingInput, secret);
    }

    private static String signature(String input, String secret) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return encode(mac.doFinal(input.getBytes(StandardCharsets.US_ASCII)));
    }

    private static String encode(byte[] value) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value);
    }

    private static byte[] decode(String value) {
        int remainder = value.length() % 4;
        String padded = remainder == 0 ? value : value + "====".substring(remainder);
        return Base64.getUrlDecoder().decode(padded);
    }
}
