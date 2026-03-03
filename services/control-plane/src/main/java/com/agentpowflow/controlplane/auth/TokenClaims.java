package com.agentpowflow.controlplane.auth;

import java.util.List;

public record TokenClaims(
        String sub,
        String tenantId,
        List<String> roles,
        String tokenType,
        long iat,
        long exp
) {
}

