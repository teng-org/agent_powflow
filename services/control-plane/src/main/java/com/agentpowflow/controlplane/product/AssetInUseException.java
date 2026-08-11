package com.agentpowflow.controlplane.product;

public class AssetInUseException extends RuntimeException {
    public AssetInUseException(String message) {
        super(message);
    }
}
