package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

@RestController
@RequestMapping("/api/v1/admin/dbviz")
public class AdminDbvizController {
    private static final Set<String> TABLES = Set.of("tenants", "users", "roles", "user_roles", "refresh_tokens");
    private static final Set<String> TENANT_SCOPED = Set.of("users", "roles", "user_roles", "refresh_tokens");

    private final JdbcTemplate jdbcTemplate;

    public AdminDbvizController(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @GetMapping("/overview")
    public Map<String, Object> overview(
            @RequestParam(name = "tenant_scope", defaultValue = "current") String tenantScope,
            Authentication authentication
    ) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        boolean allTenants = canReadAllTenants(claims, tenantScope);
        String tenantId = claims.tenantId();

        List<Map<String, Object>> items = new ArrayList<>();
        for (String table : TABLES) {
            long total = countRows(table, tenantId, allTenants);
            items.add(Map.of("table", table, "rows", total));
        }
        return Map.of(
                "status", "ok",
                "tenant_scope", allTenants ? "all" : "current",
                "tenant_id", allTenants ? "*" : tenantId,
                "items", items
        );
    }

    @GetMapping("/postgres/tables")
    public Map<String, Object> postgresTables(
            @RequestParam(name = "tenant_scope", defaultValue = "current") String tenantScope,
            Authentication authentication
    ) {
        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        boolean allTenants = canReadAllTenants(claims, tenantScope);
        String tenantId = claims.tenantId();

        List<Map<String, Object>> items = new ArrayList<>();
        for (String table : TABLES) {
            long total = countRows(table, tenantId, allTenants);
            items.add(Map.of("table", table, "rows", total));
        }
        return Map.of(
                "status", "ok",
                "items", items
        );
    }

    @GetMapping("/postgres/table/{tableName}")
    public Map<String, Object> postgresTableRows(
            @PathVariable String tableName,
            @RequestParam(name = "tenant_scope", defaultValue = "current") String tenantScope,
            @RequestParam(name = "page", defaultValue = "1") int page,
            @RequestParam(name = "page_size", defaultValue = "20") int pageSize,
            Authentication authentication
    ) {
        String table = tableName.toLowerCase();
        if (!TABLES.contains(table)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "unsupported table");
        }
        int safePage = Math.max(1, page);
        int safePageSize = Math.min(200, Math.max(1, pageSize));
        int offset = (safePage - 1) * safePageSize;

        TokenClaims claims = (TokenClaims) authentication.getPrincipal();
        boolean allTenants = canReadAllTenants(claims, tenantScope);
        String tenantId = claims.tenantId();

        String countSql = "SELECT COUNT(*) FROM " + table;
        String rowsSql = "SELECT * FROM " + table;
        List<Object> args = new ArrayList<>();
        if (TENANT_SCOPED.contains(table) && !allTenants) {
            countSql += " WHERE tenant_id = ?";
            rowsSql += " WHERE tenant_id = ?";
            args.add(tenantId);
        }

        Long totalObj = args.isEmpty()
                ? jdbcTemplate.queryForObject(countSql, Long.class)
                : jdbcTemplate.queryForObject(countSql, args.toArray(), Long.class);
        long total = totalObj == null ? 0L : totalObj;

        rowsSql += " ORDER BY 1 DESC LIMIT ? OFFSET ?";
        List<Object> rowArgs = new ArrayList<>(args);
        rowArgs.add(safePageSize);
        rowArgs.add(offset);

        List<Map<String, Object>> rows = jdbcTemplate.queryForList(rowsSql, rowArgs.toArray());
        List<Map<String, Object>> sanitized = rows.stream().map(this::sanitize).toList();

        return Map.of(
                "status", "ok",
                "table", table,
                "page", safePage,
                "page_size", safePageSize,
                "total", total,
                "items", sanitized
        );
    }

    private boolean canReadAllTenants(TokenClaims claims, String tenantScope) {
        boolean wantsAll = "all".equalsIgnoreCase(tenantScope);
        if (!wantsAll) {
            return false;
        }
        return claims.roles().contains("PLATFORM_ADMIN");
    }

    private long countRows(String table, String tenantId, boolean allTenants) {
        String sql = "SELECT COUNT(*) FROM " + table;
        if (TENANT_SCOPED.contains(table) && !allTenants) {
            Long countObj = jdbcTemplate.queryForObject(sql + " WHERE tenant_id = ?", Long.class, tenantId);
            return countObj == null ? 0L : countObj;
        }
        Long countObj = jdbcTemplate.queryForObject(sql, Long.class);
        return countObj == null ? 0L : countObj;
    }

    private Map<String, Object> sanitize(Map<String, Object> row) {
        Map<String, Object> out = new LinkedHashMap<>(row);
        Object passwordHash = out.get("password_hash");
        if (passwordHash != null) {
            out.put("password_hash", "***");
        }
        Object tokenHash = out.get("token_hash");
        if (tokenHash != null) {
            String value = String.valueOf(tokenHash);
            if (value.length() > 12) {
                out.put("token_hash", value.substring(0, 8) + "...(hidden)");
            } else {
                out.put("token_hash", "***");
            }
        }
        return out;
    }
}

