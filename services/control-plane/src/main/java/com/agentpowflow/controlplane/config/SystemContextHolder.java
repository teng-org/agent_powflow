package com.agentpowflow.controlplane.config;

public final class SystemContextHolder {
    private static final ThreadLocal<SystemContext> HOLDER = new ThreadLocal<>();

    private SystemContextHolder() {
    }

    public static void set(SystemContext context) {
        HOLDER.set(context);
    }

    public static SystemContext get() {
        return HOLDER.get();
    }

    public static void clear() {
        HOLDER.remove();
    }
}
