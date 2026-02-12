// 优先使用构建时注入的 VITE_API_BASE；否则回落到当前页面同源的 /api，避免写死 127.0.0.1
export const API_BASE =
  import.meta.env.VITE_API_BASE || `${window.location.origin}/api`;
