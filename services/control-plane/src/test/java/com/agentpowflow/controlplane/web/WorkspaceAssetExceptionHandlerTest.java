package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.product.AssetInUseException;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

class WorkspaceAssetExceptionHandlerTest {
    @Test
    void returnsStableProductConflictContract() {
        MockHttpServletRequest request = new MockHttpServletRequest("DELETE", "/api/v1/agents/agent-1");
        WorkspaceAssetExceptionHandler handler = new WorkspaceAssetExceptionHandler();

        ResponseEntity<Map<String, Object>> response = handler.assetInUse(
                new AssetInUseException("请先删除关联发布。"), request
        );

        assertEquals(HttpStatus.CONFLICT, response.getStatusCode());
        assertEquals("ASSET_IN_USE", response.getBody().get("error_code"));
        assertEquals("请先删除关联发布。", response.getBody().get("message"));
        assertEquals("/api/v1/agents/agent-1", response.getBody().get("path"));
    }
}
