package com.agentpowflow.controlplane.auth;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {
    private final TokenService tokenService;

    public JwtAuthenticationFilter(TokenService tokenService) {
        this.tokenService = tokenService;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.startsWith("/api/v1/auth/login")
                || path.startsWith("/api/v1/auth/refresh")
                || path.startsWith("/api/v1/auth/register")
                || path.startsWith("/api/v1/auth/invitations/accept")
                || path.startsWith("/api/v1/setup/status")
                || path.startsWith("/api/v1/internal/")
                || isPublishedDeploymentPath(path)
                || path.startsWith("/api/v1/system/health")
                || path.startsWith("/actuator/health");
    }

    private boolean isPublishedDeploymentPath(String path) {
        if (!path.startsWith("/api/v1/deployments/")) return false;
        String suffix = path.substring("/api/v1/deployments/".length());
        return suffix.matches("[^/]+/(?:invoke(?:/[^/]+)?|v1/chat/completions)");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        String authorization = request.getHeader("Authorization");
        if (authorization == null || !authorization.toLowerCase().startsWith("bearer ")) {
            response.setStatus(HttpStatus.UNAUTHORIZED.value());
            response.setContentType("application/json");
            response.getWriter().write("{\"status\":\"error\",\"detail\":\"missing authorization\"}");
            return;
        }
        String token = authorization.substring(7).trim();
        try {
            TokenClaims claims = tokenService.verify(token, "access");
            List<SimpleGrantedAuthority> authorities = new ArrayList<>();
            for (String role : claims.roles()) {
                authorities.add(new SimpleGrantedAuthority("ROLE_" + role));
            }
            UsernamePasswordAuthenticationToken auth = new UsernamePasswordAuthenticationToken(
                    claims,
                    null,
                    authorities
            );
            SecurityContextHolder.getContext().setAuthentication(auth);
            request.setAttribute("auth.tenant_id", claims.tenantId());
            request.setAttribute("auth.actor_id", claims.sub());
            request.setAttribute("auth.roles", claims.roles());
            request.setAttribute("auth.jwt_key_slot", claims.keySlot());
            request.setAttribute("auth.jwt_legacy_format", claims.legacyFormat());
            filterChain.doFilter(request, response);
        } catch (ResponseStatusException ex) {
            response.setStatus(ex.getStatusCode().value());
            response.setContentType("application/json");
            response.getWriter().write("{\"status\":\"error\",\"detail\":\"" + ex.getReason() + "\"}");
        } finally {
            SecurityContextHolder.clearContext();
        }
    }
}
