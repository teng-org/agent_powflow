<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RUNTIME_CONFIG_DEFAULTS } from '../constants/runtimeConfigDefaults'
import vModalFocus from './common/modalFocusDirective'
import vInteractiveRow from './common/interactiveRowDirective'

const props = defineProps({
  platformSettingState: { type: Object, required: true },
  loadRuntimeConfig: { type: Function, required: true },
  saveRuntimeConfig: { type: Function, required: true },
  previewRuntimeConfigImpact: { type: Function, required: false, default: null },
  loadConfigHistory: { type: Function, required: false, default: null },
  loadConfigAudit: { type: Function, required: false, default: null },
  loadConfigDiff: { type: Function, required: false, default: null },
  rollbackConfigRelease: { type: Function, required: false, default: null },
  switchDefaultConfig: { type: Function, required: false, default: null },
  deleteConfigRelease: { type: Function, required: false, default: null },
  sectionDomId: { type: Function, default: null },
})

const sectionMeta = [
  { id: 'auth', title: '认证与安全' },
  { id: 'system', title: '系统基础' },
  { id: 'embedding_summary', title: 'Embedding 与总结' },
  { id: 'chat', title: '对话核心' },
  { id: 'memory', title: '记忆策略' },
  { id: 'api', title: 'API 运行治理' },
  { id: 'pricing', title: '模型定价' },
]

const fieldDefs = [
  { key: 'execution_admin_token', label: '执行管理员令牌', section: 'auth', type: 'text', help: 'Execution 管理员静态令牌。' },
  { key: 'execution_admin_username', label: '执行管理员用户名', section: 'auth', type: 'text', help: 'Execution 管理登录账号。' },
  { key: 'execution_admin_password', label: '执行管理员密码', section: 'auth', type: 'password', help: 'Execution 管理登录密码。' },
  { key: 'execution_admin_role', label: '执行管理员角色', section: 'auth', type: 'text', help: '执行平面的管理员默认角色。' },
  { key: 'execution_jwt_secret', label: 'JWT 签名密钥', section: 'auth', type: 'password', help: '用于签发/校验 JWT 的密钥。' },
  { key: 'execution_jwt_ttl_seconds', label: 'JWT 有效期（秒）', section: 'auth', type: 'number', min: 60, step: 60, help: '管理员 JWT 过期时间。' },

  { key: 'execution_env', label: '运行环境', section: 'system', type: 'select', options: ['dev', 'test', 'prod'], help: '服务运行环境标识。' },
  { key: 'execution_audit_enabled', label: '审计开关', section: 'system', type: 'boolean', help: '是否启用执行平面审计日志。' },
  { key: 'execution_audit_base_dir', label: '审计目录', section: 'system', type: 'text', help: '审计日志文件落盘目录。' },
  { key: 'system_log_storage_path', label: '系统日志存储路径', section: 'system', type: 'text', help: '系统日志主目录；修改时需选择历史数据处理策略。' },
  { key: 'system_log_hot_retention_days', label: '热日志保留天数', section: 'system', type: 'number', min: 1, step: 1, help: '可直接在线查询的日志保留天数。' },
  { key: 'system_log_archive_retention_days', label: '归档窗口上限天数', section: 'system', type: 'number', min: 1, step: 1, help: '超过热日志天数后至该天数内可压缩归档。' },
  { key: 'system_log_archive_enabled', label: '日志归档开关', section: 'system', type: 'boolean', help: '是否启用30天外日志压缩归档。' },
  { key: 'system_log_archive_period', label: '归档周期', section: 'system', type: 'select', options: ['month', 'week', 'day'], help: '日志压缩归档按年月/周/日分包。' },
  { key: 'system_log_expired_action', label: '超期日志动作', section: 'system', type: 'select', options: ['delete', 'keep'], help: '归档窗口外日志默认动作。' },
  { key: 'system_log_path_change_strategy', label: '路径变更历史处理', section: 'system', type: 'select', options: ['migrate', 'keep', 'delete'], help: '日志目录变更后历史数据处理策略。' },
  { key: 'global_kb_id', label: '全局知识库 ID', section: 'system', type: 'text', help: '系统默认全局知识库标识。' },
  { key: 'execution_cors_origins', label: 'CORS 允许来源', section: 'system', type: 'textarea', help: '允许跨域来源列表（逗号分隔）。' },

  { key: 'embedding_mode', label: 'Embedding 模式', section: 'embedding_summary', type: 'select', options: ['api_strict', 'local_only'], help: 'API 模式下端点与 Key 仅从 API 管理读取。' },
  { key: 'embedding_max_chars', label: 'Embedding 最大字符', section: 'embedding_summary', type: 'number', min: 100, step: 10, help: '单次嵌入请求允许的最大字符数。' },
  { key: 'summary_segment_chars', label: '总结切段长度', section: 'embedding_summary', type: 'number', min: 100, step: 10, help: '文档总结切分时每段字符数。' },
  { key: 'summary_segment_stable', label: '总结稳定切段', section: 'embedding_summary', type: 'boolean', help: '是否启用稳定切段策略。' },
  { key: 'summary_segment_unit_max_chars', label: '切段单元上限', section: 'embedding_summary', type: 'number', min: 100, step: 10, help: '单个切分单元最大字符数。' },
  { key: 'summary_segment_overlap', label: '切段重叠字符', section: 'embedding_summary', type: 'number', min: 0, step: 5, help: '相邻切段重叠字符数。' },
  { key: 'summary_segment_max_chunks', label: '最大切段数', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '单文档总结最多切段数量。' },
  { key: 'summary_segment_concurrency', label: '总结并发数', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '总结阶段并发处理数。' },
  { key: 'summary_min_chars', label: '总结最小字符', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '触发总结的最小字符阈值。' },
  { key: 'summary_max_chars', label: '总结最大字符', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '单次总结输入最大字符数。' },
  { key: 'summary_overview_max_chars', label: '概览总结上限', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '概览摘要输出最大字符数。' },
  { key: 'summary_doc_max_tokens', label: '文档总结最大 Token', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '单文档总结最大 token。' },
  { key: 'summary_repair_max_tokens', label: '修复总结最大 Token', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '总结修复步骤最大 token。' },
  { key: 'summary_kb_batch_size', label: '知识库批处理大小', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '知识库总结每批文档数量。' },
  { key: 'summary_kb_batch_max_tokens', label: '批处理最大 Token', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '知识库批处理总结 token 上限。' },
  { key: 'summary_kb_max_chars', label: '知识库总结最大字符', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '知识库总结输入字符上限。' },
  { key: 'summary_kb_final_max_tokens', label: '知识库最终总结 Token', section: 'embedding_summary', type: 'number', min: 1, step: 1, help: '知识库最终汇总总结 token 上限。' },

  { key: 'chat_system_prompt', label: '系统提示词', section: 'chat', type: 'textarea', help: 'Chat 系统级提示词模板。' },
  { key: 'chat_default_top_k', label: '默认检索 Top-K', section: 'chat', type: 'number', min: 1, step: 1, help: '默认召回条数。' },
  { key: 'chat_default_rank_method', label: '默认排序策略', section: 'chat', type: 'select', options: ['vector', 'hybrid', 'keyword'], help: '检索排序策略。' },
  { key: 'chat_agent_enabled', label: 'Agent 默认开关', section: 'chat', type: 'boolean', help: '控制 5173 对话默认是否启用 Agent 工具循环；单次对话仍可临时关闭或开启。' },
  { key: 'chat_agent_tool_planning_enabled', label: 'Agent LLM 规划器', section: 'chat', type: 'boolean', help: '启用后由 LLM 决定 rag_search / aux_summarize / final；关闭时使用固定检索流程。' },
  { key: 'chat_agent_aux_summary_tool_enabled', label: 'Agent 辅助总结工具', section: 'chat', type: 'boolean', help: '允许 Agent 在检索后调用辅助 LLM 总结证据块。' },
  { key: 'chat_agent_tool_max_rounds', label: 'Agent 最大轮数', section: 'chat', type: 'number', min: 1, max: 8, step: 1, help: '单次对话最多执行的工具规划轮数。' },
  { key: 'chat_agent_summary_max_chunks', label: 'Agent 总结块数', section: 'chat', type: 'number', min: 1, max: 30, step: 1, help: '辅助总结工具最多汇总的检索切片数量。' },
  { key: 'chat_agent_summary_max_tokens', label: 'Agent 总结 Token', section: 'chat', type: 'number', min: 160, max: 2000, step: 10, help: '辅助总结工具输出的最大 token。' },
  { key: 'chat_session_title_max_attempts', label: '标题生成最大轮数', section: 'chat', type: 'number', min: 1, max: 3, step: 1, help: '会话标题最多尝试 1-3 轮；弱标题会被继续修正。' },
  { key: 'chat_history_store_backend', label: '会话存储后端', section: 'chat', type: 'select', options: ['postgres_prefer', 'postgres', 'sqlite', 'json'], help: '会话消息存储后端。' },
  { key: 'chat_history_store_path', label: '会话存储路径', section: 'chat', type: 'text', help: '本地 JSON 存储模式下的文件路径。' },

  { key: 'memory_auto_summary_enabled', label: '自动总结', section: 'memory', type: 'boolean', help: '是否自动触发会话记忆总结。' },
  { key: 'memory_hierarchical_enabled', label: '分层记忆', section: 'memory', type: 'boolean', help: '是否启用分层记忆策略。' },
  { key: 'memory_user_profile_enabled', label: '用户画像记忆', section: 'memory', type: 'boolean', help: '是否启用用户画像记忆。' },
  { key: 'memory_eviction_enabled', label: '记忆淘汰', section: 'memory', type: 'boolean', help: '是否启用记忆淘汰机制。' },
  { key: 'memory_recent_messages_limit', label: '最近消息数量', section: 'memory', type: 'number', min: 1, step: 1, help: '直接保留在上下文中的最近消息条数。' },
  { key: 'memory_context_char_budget', label: '记忆上下文预算', section: 'memory', type: 'number', min: 1000, step: 100, help: '注入记忆的字符预算。' },
  { key: 'memory_compress_trigger_messages', label: '触发压缩消息数', section: 'memory', type: 'number', min: 1, step: 1, help: '达到该消息数后触发压缩。' },
  { key: 'memory_short_summary_max_chars', label: '短摘要上限', section: 'memory', type: 'number', min: 1, step: 1, help: '短摘要最大字符数。' },
  { key: 'memory_long_summary_max_chars', label: '长摘要上限', section: 'memory', type: 'number', min: 1, step: 1, help: '长摘要最大字符数。' },
  { key: 'memory_profile_max_chars', label: '画像摘要上限', section: 'memory', type: 'number', min: 1, step: 1, help: '用户画像最大字符数。' },
  { key: 'memory_item_max', label: '记忆条目上限', section: 'memory', type: 'number', min: 1, step: 1, help: '会话可保留的记忆条目数上限。' },
  { key: 'memory_query_rewrite_enabled', label: '查询改写开关', section: 'memory', type: 'boolean', help: '是否开启检索查询改写。' },
  { key: 'memory_query_rewrite_use_llm', label: '改写使用 LLM', section: 'memory', type: 'boolean', help: '查询改写是否使用 LLM。' },
  { key: 'memory_query_rewrite_max_chars', label: '改写最大字符', section: 'memory', type: 'number', min: 1, step: 1, help: '查询改写最大字符数。' },
  { key: 'memory_query_rewrite_min_chars', label: '改写最小字符', section: 'memory', type: 'number', min: 1, step: 1, help: '查询改写最小字符数。' },
  { key: 'memory_query_rewrite_min_overlap_ratio', label: '改写最小重叠比', section: 'memory', type: 'number', min: 0, step: 0.01, help: '改写前后语义重叠比下限。' },
  { key: 'memory_query_rewrite_max_len_ratio', label: '改写最大长度比', section: 'memory', type: 'number', min: 0, step: 0.01, help: '改写后长度相对原查询的最大倍数。' },
  { key: 'memory_session_rag_enabled', label: '会话 RAG 开关', section: 'memory', type: 'boolean', help: '是否启用会话级 RAG。' },
  { key: 'memory_session_rag_top_k', label: '会话 RAG Top-K', section: 'memory', type: 'number', min: 1, step: 1, help: '会话 RAG 召回条数。' },
  { key: 'memory_session_keyword_top_k', label: '关键词召回 Top-K', section: 'memory', type: 'number', min: 1, step: 1, help: '关键词召回条数。' },
  { key: 'memory_session_vector_enabled', label: '向量召回开关', section: 'memory', type: 'boolean', help: '是否启用向量召回。' },
  { key: 'memory_session_vector_top_k', label: '向量召回 Top-K', section: 'memory', type: 'number', min: 1, step: 1, help: '向量召回条数。' },
  { key: 'memory_session_fusion_weight_keyword', label: '融合权重-关键词', section: 'memory', type: 'number', min: 0, step: 0.01, help: '融合打分中关键词权重。' },
  { key: 'memory_session_fusion_weight_vector', label: '融合权重-向量', section: 'memory', type: 'number', min: 0, step: 0.01, help: '融合打分中向量权重。' },
  { key: 'memory_session_fusion_weight_recency', label: '融合权重-时效', section: 'memory', type: 'number', min: 0, step: 0.01, help: '融合打分中时效权重。' },
  { key: 'memory_session_fusion_hybrid_bonus', label: '混合命中加权', section: 'memory', type: 'number', min: 0, step: 0.01, help: '同时命中多策略时的加权奖励。' },
  { key: 'memory_session_fusion_recency_half_life_days', label: '时效半衰期（天）', section: 'memory', type: 'number', min: 0, step: 0.1, help: '时效衰减半衰期。' },
  { key: 'memory_recent_profile_activity_limit', label: '近期画像活动数', section: 'memory', type: 'number', min: 1, step: 1, help: '画像最近活动保留上限。' },
  { key: 'memory_recent_activity_ttl_seconds', label: '近期活动 TTL（秒）', section: 'memory', type: 'number', min: 1, step: 1, help: '近期活动数据有效期。' },
  { key: 'memory_recent_activity_cleanup_interval_seconds', label: '活动清理间隔（秒）', section: 'memory', type: 'number', min: 1, step: 1, help: '近期活动清理任务执行间隔。' },
  { key: 'chat_session_vector_collection', label: '会话向量集合名', section: 'memory', type: 'text', help: '会话向量索引集合名。' },
  { key: 'chat_session_vector_timeout_seconds', label: '会话向量超时（秒）', section: 'memory', type: 'number', min: 1, step: 1, help: '会话向量查询超时时间。' },

  { key: 'api_slot_max_per_endpoint', label: '端点并发槽位', section: 'api', type: 'number', min: 1, step: 1, help: '每个 API 端点最大并发槽位。' },
  { key: 'api_slot_wait_seconds', label: '槽位等待（秒）', section: 'api', type: 'number', min: 1, step: 1, help: '等待可用槽位的最大时长。' },
  { key: 'api_slot_ttl_seconds', label: '槽位 TTL（秒）', section: 'api', type: 'number', min: 1, step: 1, help: '槽位占用的过期时间。' },
  { key: 'api_daily_token_threshold', label: 'Token 日告警阈值', section: 'api', type: 'number', min: 0, step: 1000, help: '平台维度 token 日用量告警阈值。' },
  { key: 'api_daily_cost_threshold', label: '消费日告警阈值（USD）', section: 'api', type: 'number', min: 0, step: 0.01, help: '平台维度日消费告警阈值。' },
  { key: 'api_concurrency_threshold', label: '并发日告警阈值', section: 'api', type: 'number', min: 0, step: 1, help: '系统并发峰值告警阈值。' },
  { key: 'api_reconciliation_diff_ratio_threshold', label: '对账差异率阈值', section: 'api', type: 'number', min: 0, step: 0.01, help: '对账偏差告警触发阈值（比例）。' },
  { key: 'api_reconciliation_min_estimated_cost_usd', label: '对账最小金额阈值（USD）', section: 'api', type: 'number', min: 0, step: 0.01, help: '低于该估算金额不触发对账偏差告警。' },
  { key: 'api_key_expiry_alert_days', label: 'Key 到期告警阈值天数', section: 'api', type: 'text', help: '逗号分隔阈值，例如 7,3,1。' },
  { key: 'api_key_expiry_alert_scan_limit', label: 'Key 到期扫描上限', section: 'api', type: 'number', min: 1, step: 1, help: '每次到期告警评估扫描的最大端点数。' },
  { key: 'api_key_alert_suppress_seconds', label: 'Key 告警抑制窗口（秒）', section: 'api', type: 'number', min: 0, step: 1, help: '同端点告警短时间重复触发时进入抑制。' },
  { key: 'api_key_alert_escalate_trigger_count', label: 'Key 告警升级触发次数', section: 'api', type: 'number', min: 1, step: 1, help: '重复触发达到次数后自动升级风险级别。' },
  { key: 'api_key_alert_muted_seconds', label: 'Key 告警静默时长（秒）', section: 'api', type: 'number', min: 0, step: 1, help: '告警状态置为 MUTED 时自动应用的静默时长。' },
  { key: 'api_key_alert_notify_webhook_url', label: 'Key 告警通知 Webhook', section: 'api', type: 'text', help: '可选。填写后新开告警会异步推送摘要。' },
  { key: 'api_key_alert_notify_cooldown_seconds', label: 'Key 通知冷却（秒）', section: 'api', type: 'number', min: 0, step: 1, help: '同告警短时间内不重复推送通知。' },
  { key: 'api_key_alert_notify_timeout_seconds', label: 'Key 通知超时（秒）', section: 'api', type: 'number', min: 1, step: 1, help: 'Webhook 通知请求超时时间。' },
  { key: 'api_key_alert_notify_retry_max', label: 'Key 通知重试次数', section: 'api', type: 'number', min: 0, step: 1, help: 'Webhook 推送失败时最大重试次数。' },
  { key: 'api_key_alert_notify_retry_backoff_ms', label: 'Key 通知重试退避（毫秒）', section: 'api', type: 'number', min: 0, step: 50, help: 'Webhook 重试线性退避基数。' },
]

const showModal = ref(false)
const showSaveConfirmModal = ref(false)
const activeSectionId = ref('')
const draft = reactive({})
const pricingDraftRows = ref([])
const showLongTextModal = ref(false)
const longTextFieldKey = ref('')
const longTextFieldLabel = ref('')
const longTextDraft = ref('')
const historyLoading = ref(false)
const releaseNoteDraft = ref('')
const timeDisplayMode = ref('relative') // relative | exact
const showReleaseModal = ref(false)
const releaseModalTab = ref('info') // info | diff
const selectedReleaseId = ref('')
const compareTargetReleaseId = ref('')
const saveEffectHint = ref('')
const confirmCopyHint = ref('')
const impactPreview = ref(null)
const impactLoading = ref(false)
const impactError = ref('')
const saveConflictDetected = ref(false)
const saveConflictHint = ref('')
const saveConflictDiffRows = ref([])
const auditLoading = ref(false)
const selectedAuditId = ref('')
const auditDiffKeyQuery = ref('')
const auditDiffKind = ref('all') // all | added | removed | modified
const expandedAuditDiffKey = ref('')
const auditFilters = reactive({
  config_key: '',
  actor_id: '',
  action: '',
  release_id: 0,
  trace_id: '',
  from_ts: '',
  to_ts: '',
  limit: 200,
})

const sectionFields = computed(() => {
  const map = {}
  for (const section of sectionMeta) {
    map[section.id] = fieldDefs.filter((x) => x.section === section.id)
  }
  return map
})

const schemaByKey = computed(() => {
  const rows = Array.isArray(props.platformSettingState.runtimeConfigSchema) ? props.platformSettingState.runtimeConfigSchema : []
  const map = {}
  for (const row of rows) {
    const key = String(row?.config_key || '')
    if (!key) continue
    map[key] = row
  }
  return map
})

const validationErrorMap = computed(() => {
  const rows = Array.isArray(props.platformSettingState.runtimeConfigValidationErrors) ? props.platformSettingState.runtimeConfigValidationErrors : []
  const map = {}
  const fieldLabelMap = Object.fromEntries(fieldDefs.map((d) => [d.key, d.label]))
  function humanize(row) {
    const code = String(row?.code || '').toLowerCase()
    const key = String(row?.key || '')
    const label = fieldLabelMap[key] || key
    const value = row?.value
    const constraint = row?.constraint || {}
    if (code === 'enum') return `${label}取值不合法，请使用允许值。`
    if (code === 'min') return `${label}不能小于${constraint?.min ?? ''}。`
    if (code === 'max') return `${label}不能大于${constraint?.max ?? ''}。`
    if (code === 'type') return `${label}格式错误，请输入数字。`
    if (code === 'dependency') return `${label}依赖条件未满足，请先开启相关开关。`
    if (code === 'sum') return `融合权重总和不能超过 1.0。`
    if (code === 'range') {
      if (key === 'summary_min_chars') return `总结最小字符不能大于总结最大字符。`
      if (key === 'summary_max_chars') return `总结最大字符必须大于或等于总结最小字符。`
      if (key === 'summary_segment_overlap') return `切段重叠字符必须小于切段长度。`
      if (key === 'summary_segment_unit_max_chars') return `切段单元上限不能超过切段长度。`
      if (key === 'api_slot_ttl_seconds') return `槽位 TTL 必须大于或等于槽位等待时长。`
      if (key === 'system_log_archive_retention_days') return `归档窗口天数必须大于或等于热日志保留天数。`
      return `${label}与相关参数的范围关系不合法。`
    }
    if (code === 'unsupported_key') return `存在不支持的配置项：${key}。`
    const raw = String(row?.message || '').trim()
    if (raw) return `${label}校验失败：${raw}`
    if (value != null) return `${label}校验失败，当前值：${value}`
    return `${label}校验失败。`
  }
  for (const row of rows) {
    const key = String(row?.key || '')
    const message = humanize(row)
    if (!key || !message || map[key]) continue
    map[key] = message
  }
  return map
})

const mergedConfig = computed(() => ({
  ...RUNTIME_CONFIG_DEFAULTS,
  ...(props.platformSettingState.runtimeConfig || {}),
}))
const knownConfigKeys = computed(() => {
  const keys = new Set(Object.keys(RUNTIME_CONFIG_DEFAULTS || {}))
  for (const def of fieldDefs) keys.add(def.key)
  const defaults = props.platformSettingState.runtimeConfigDefaults || {}
  for (const k of Object.keys(defaults)) keys.add(k)
  const current = props.platformSettingState.runtimeConfig || {}
  for (const k of Object.keys(current || {})) keys.add(k)
  return keys
})

function normalize(value) {
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function getCurrentValue(key) {
  return mergedConfig.value[key]
}

function getDefaultValue(key) {
  return props.platformSettingState.runtimeConfigDefaults?.[key] ?? RUNTIME_CONFIG_DEFAULTS[key]
}

function getValidationMeta(def) {
  const schema = schemaByKey.value?.[def.key]
  const validation = schema?.validation || {}
  return {
    min: validation?.min ?? def.min ?? null,
    max: validation?.max ?? null,
    step: validation?.step ?? def.step ?? null,
    enum: Array.isArray(validation?.enum) ? validation.enum : Array.isArray(def.options) ? def.options : null,
  }
}

function getFieldSchema(def) {
  return schemaByKey.value?.[def.key] || null
}

function isFieldHotReload(def) {
  const schema = getFieldSchema(def)
  if (typeof schema?.hot_reload === 'boolean') return schema.hot_reload
  return true
}

function isFieldVersioned(def) {
  const schema = getFieldSchema(def)
  if (typeof schema?.versioned === 'boolean') return schema.versioned
  return true
}

function fieldError(def) {
  return validationErrorMap.value?.[def.key] || ''
}

function riskLevelLabel(level) {
  const v = String(level || '').toUpperCase()
  if (v === 'HIGH') return '高'
  if (v === 'MEDIUM') return '中'
  if (v === 'LOW') return '低'
  return '-'
}

const selectedAuditRow = computed(() => {
  const id = String(selectedAuditId.value || '')
  if (!id) return null
  return (props.platformSettingState.configAuditItems || []).find((x) => String(x.audit_id) === id) || null
})

function stableStringify(v) {
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

function normalizeObj(v) {
  if (v && typeof v === 'object' && !Array.isArray(v)) return v
  return {}
}

const selectedAuditDiffRows = computed(() => {
  const row = selectedAuditRow.value
  if (!row) return []
  const oldObj = normalizeObj(row.old_json)
  const newObj = normalizeObj(row.new_json)
  const keys = Array.from(new Set([...Object.keys(oldObj), ...Object.keys(newObj)])).sort((a, b) => a.localeCompare(b))
  return keys
    .map((key) => ({
      key,
      oldVal: oldObj[key],
      newVal: newObj[key],
      kind:
        !(key in oldObj) && key in newObj
          ? 'added'
          : key in oldObj && !(key in newObj)
            ? 'removed'
            : 'modified',
      changed: stableStringify(oldObj[key]) !== stableStringify(newObj[key]),
    }))
    .filter((x) => x.changed)
})

const filteredAuditDiffRows = computed(() => {
  let rows = [...selectedAuditDiffRows.value]
  const q = String(auditDiffKeyQuery.value || '').trim().toLowerCase()
  const kind = String(auditDiffKind.value || 'all')
  if (q) rows = rows.filter((x) => String(x.key || '').toLowerCase().includes(q))
  if (kind !== 'all') rows = rows.filter((x) => x.kind === kind)
  return rows
})

function toggleAuditDiffExpand(key) {
  const k = String(key || '')
  expandedAuditDiffKey.value = expandedAuditDiffKey.value === k ? '' : k
}

function formatJsonPretty(v) {
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

function diffKindLabel(kind) {
  if (kind === 'added') return '新增'
  if (kind === 'removed') return '删除'
  return '修改'
}

function formatDisplay(def, value) {
  const raw = value == null || value === '' ? getDefaultValue(def.key) : value
  if (raw == null || raw === '') return '-'
  const isFallback = value == null || value === ''
  const suffix = isFallback ? '（default）' : ''
  if (def.type === 'boolean') return `${raw ? '开启' : '关闭'}${suffix}`
  if (def.type === 'password') return raw ? `已设置${suffix}` : '未设置'
  const text = String(raw)
  const out = text.length > 88 ? `${text.slice(0, 88)}...` : text
  return `${out}${suffix}`
}

function pricingRowsFromObject(obj) {
  return Object.entries(obj || {}).map(([model, price]) => ({
    model,
    input_per_1k: Number(price?.input_per_1k ?? 0),
    output_per_1k: Number(price?.output_per_1k ?? 0),
  }))
}

function pricingObjectFromRows(rows) {
  const out = {}
  for (const row of rows) {
    const model = String(row.model || '').trim()
    if (!model) continue
    out[model] = {
      input_per_1k: Number(row.input_per_1k || 0),
      output_per_1k: Number(row.output_per_1k || 0),
    }
  }
  return out
}

function sectionDirty(sectionId) {
  if (sectionId === 'pricing') {
    const current = getCurrentValue('api_model_pricing') || {}
    const defaults = getDefaultValue('api_model_pricing') || {}
    return normalize(current) !== normalize(defaults)
  }
  const defs = sectionFields.value[sectionId] || []
  return defs.some((def) => normalize(getCurrentValue(def.key)) !== normalize(getDefaultValue(def.key)))
}

function openSectionEditor(sectionId) {
  props.platformSettingState.error = ''
  props.platformSettingState.runtimeConfigValidationErrors = []
  activeSectionId.value = sectionId
  if (sectionId === 'pricing') {
    pricingDraftRows.value = pricingRowsFromObject(getCurrentValue('api_model_pricing') || {})
  } else {
    for (const def of sectionFields.value[sectionId] || []) {
      const current = getCurrentValue(def.key)
      draft[def.key] = current == null || current === '' ? getDefaultValue(def.key) : current
    }
  }
  showModal.value = true
}

function closeEditor() {
  showModal.value = false
  setTimeout(() => {
    activeSectionId.value = ''
    pricingDraftRows.value = []
    showSaveConfirmModal.value = false
    confirmCopyHint.value = ''
  }, 160)
}

function openLongTextEditor(def) {
  longTextFieldKey.value = def.key
  longTextFieldLabel.value = def.label
  longTextDraft.value = String(draft[def.key] || '')
  showLongTextModal.value = true
}

function closeLongTextEditor() {
  showLongTextModal.value = false
}

function applyLongTextEditor() {
  if (!longTextFieldKey.value) return
  draft[longTextFieldKey.value] = longTextDraft.value
  showLongTextModal.value = false
}

function clearValidationUiOnEdit() {
  if (Array.isArray(props.platformSettingState.runtimeConfigValidationErrors) && props.platformSettingState.runtimeConfigValidationErrors.length) {
    props.platformSettingState.runtimeConfigValidationErrors = []
  }
  if (props.platformSettingState.error) {
    props.platformSettingState.error = ''
  }
}

function castByType(def, value) {
  if (def.type === 'number') return Number(value || 0)
  if (def.type === 'boolean') return value === true
  return value == null ? '' : String(value)
}

function validateDraft(sectionId) {
  if (sectionId === 'pricing') {
    const hasInvalid = pricingDraftRows.value.some((row) => Number(row.input_per_1k) < 0 || Number(row.output_per_1k) < 0)
    if (hasInvalid) return '模型定价不能为负数。'
    return ''
  }
  const defs = sectionFields.value[sectionId] || []
  for (const def of defs) {
    const meta = getValidationMeta(def)
    if (def.type === 'number') {
      const value = Number(draft[def.key])
      if (!Number.isFinite(value)) return `${def.label} 必须是数字`
      if (meta.min != null && value < Number(meta.min)) {
        return `${def.label} 不能小于 ${meta.min}`
      }
      if (meta.max != null && value > Number(meta.max)) {
        return `${def.label} 不能大于 ${meta.max}`
      }
    }
    if (meta.enum && meta.enum.length) {
      const value = String(draft[def.key] ?? '')
      if (!meta.enum.includes(value)) {
        return `${def.label} 必须是允许值：${meta.enum.join(' / ')}`
      }
    }
  }
  return ''
}

function collectSectionPatch(sectionId) {
  if (!sectionId) return {}
  if (sectionId === 'pricing') {
    const nextVal = pricingObjectFromRows(pricingDraftRows.value)
    const currentVal = getCurrentValue('api_model_pricing') || {}
    if (stableJson(nextVal) === stableJson(currentVal)) return {}
    return { api_model_pricing: nextVal }
  }
  const patch = {}
  for (const def of sectionFields.value[sectionId] || []) {
    const nextVal = castByType(def, draft[def.key])
    const currentVal = getCurrentValue(def.key)
    if (stableJson(nextVal) !== stableJson(currentVal)) {
      patch[def.key] = nextVal
    }
  }
  return patch
}

const pendingPatch = computed(() => collectSectionPatch(activeSectionId.value))
const pendingPatchKeys = computed(() => Object.keys(pendingPatch.value || {}))
const saveImpactSummary = computed(() => {
  const keys = pendingPatchKeys.value
  let hot = 0
  let restart = 0
  let versioned = 0
  let nonVersioned = 0
  for (const key of keys) {
    const schema = schemaByKey.value?.[key] || {}
    const isHot = schema?.hot_reload !== false
    const isVer = schema?.versioned !== false
    if (isHot) hot += 1
    else restart += 1
    if (isVer) versioned += 1
    else nonVersioned += 1
  }
  return {
    total: keys.length,
    hot,
    restart,
    versioned,
    nonVersioned,
  }
})

async function saveSection() {
  if (!pendingPatchKeys.value.length) return
  const note = String(releaseNoteDraft.value || '').trim()
  if (note && note.length < 8) {
    props.platformSettingState.error = '发布备注至少 8 个字符，请补充变更原因。'
    return
  }
  impactLoading.value = true
  impactError.value = ''
  impactPreview.value = null
  try {
    if (props.previewRuntimeConfigImpact) {
      impactPreview.value = await props.previewRuntimeConfigImpact(pendingPatch.value || {})
    }
  } catch (err) {
    impactError.value = String(err?.message || err || '影响面分析失败')
  } finally {
    impactLoading.value = false
  }
  showSaveConfirmModal.value = true
}

function closeSaveConfirmModal() {
  showSaveConfirmModal.value = false
  confirmCopyHint.value = ''
  impactPreview.value = null
  impactError.value = ''
  saveConflictDetected.value = false
  saveConflictHint.value = ''
  saveConflictDiffRows.value = []
}

const confirmSummaryText = computed(() => {
  const impact = impactPreview.value?.impact || {}
  const lines = []
  lines.push(`分组: ${modalTitle.value}`)
  lines.push(`变更项: ${saveImpactSummary.value.total}`)
  lines.push(`热更新: ${saveImpactSummary.value.hot}`)
  lines.push(`需重启: ${saveImpactSummary.value.restart}`)
  lines.push(`版本化: ${saveImpactSummary.value.versioned}`)
  lines.push(`非版本化: ${saveImpactSummary.value.nonVersioned}`)
  if (impact?.risk_level) lines.push(`风险级别: ${impact.risk_level}`)
  if (Array.isArray(impact?.impact_services) && impact.impact_services.length) lines.push(`影响服务: ${impact.impact_services.join(', ')}`)
  if (Array.isArray(impact?.impact_modules) && impact.impact_modules.length) lines.push(`影响模块: ${impact.impact_modules.join(', ')}`)
  if (Array.isArray(impact?.reasons) && impact.reasons.length) lines.push(`风险说明: ${impact.reasons.join('；')}`)
  if (releaseNoteDraft.value) lines.push(`备注: ${releaseNoteDraft.value}`)
  lines.push(`键: ${pendingPatchKeys.value.join(', ') || '-'}`)
  return lines.join('\n')
})

async function copyConfirmSummary() {
  try {
    await navigator.clipboard.writeText(confirmSummaryText.value)
    confirmCopyHint.value = '已复制'
  } catch {
    confirmCopyHint.value = '复制失败'
  }
}

async function confirmAndSaveSection() {
  const sectionId = activeSectionId.value
  const error = validateDraft(sectionId)
  if (error) {
    props.platformSettingState.error = error
    return
  }
  props.platformSettingState.error = ''
  const patch = collectSectionPatch(sectionId)
  const keys = Object.keys(patch)
  if (!keys.length) {
    saveEffectHint.value = '无变更，未保存'
    closeSaveConfirmModal()
    closeEditor()
    return
  }
  const restartKeys = keys.filter((key) => schemaByKey.value?.[key]?.hot_reload === false)

  if (sectionId === 'pricing') {
    saveConflictDetected.value = false
    saveConflictHint.value = ''
    const ok = await props.saveRuntimeConfig(patch, { note: releaseNoteDraft.value })
    if (!ok) {
      const msg = String(props.platformSettingState.error || '')
      if (msg.includes('配置冲突')) {
        await refreshConfigHistory()
        const conflict = props.platformSettingState.runtimeConfigConflict || {}
        const actualReleaseId = Number(conflict?.actual_latest_release_id || 0)
        const latest = releaseRows.value.find((x) => Number(x?.release_id || 0) === actualReleaseId) || releaseRows.value[1] || null
        const snapshot = (latest?.snapshot_json && typeof latest.snapshot_json === 'object') ? latest.snapshot_json : {}
        saveConflictDiffRows.value = Object.keys(patch)
          .map((key) => ({ key, latest: snapshot[key], draft: patch[key] }))
          .filter((x) => stableJson(x.latest) !== stableJson(x.draft))
        saveConflictDetected.value = true
        const expected = Number(conflict?.expected_latest_release_id || 0)
        const actual = Number(conflict?.actual_latest_release_id || 0)
        if (expected && actual) {
          saveConflictHint.value = `检测到新版本冲突（你的基线 #${expected}，当前最新 #${actual}），已自动刷新发布历史。`
        } else {
          saveConflictHint.value = '检测到新版本冲突，已自动刷新发布历史。'
        }
        return
      }
      closeSaveConfirmModal()
      return
    }
    saveEffectHint.value = restartKeys.length ? `已保存，需重启生效：${restartKeys.join(', ')}` : '已保存，立即生效'
    closeSaveConfirmModal()
    releaseNoteDraft.value = ''
    closeEditor()
    return
  }

  saveConflictDetected.value = false
  saveConflictHint.value = ''
  const ok = await props.saveRuntimeConfig(patch, { note: releaseNoteDraft.value })
  if (!ok) {
    const msg = String(props.platformSettingState.error || '')
    if (msg.includes('配置冲突')) {
      await refreshConfigHistory()
      const conflict = props.platformSettingState.runtimeConfigConflict || {}
      const actualReleaseId = Number(conflict?.actual_latest_release_id || 0)
      const latest = releaseRows.value.find((x) => Number(x?.release_id || 0) === actualReleaseId) || releaseRows.value[1] || null
      const snapshot = (latest?.snapshot_json && typeof latest.snapshot_json === 'object') ? latest.snapshot_json : {}
      saveConflictDiffRows.value = Object.keys(patch)
        .map((key) => ({ key, latest: snapshot[key], draft: patch[key] }))
        .filter((x) => stableJson(x.latest) !== stableJson(x.draft))
      saveConflictDetected.value = true
      const expected = Number(conflict?.expected_latest_release_id || 0)
      const actual = Number(conflict?.actual_latest_release_id || 0)
      if (expected && actual) {
        saveConflictHint.value = `检测到新版本冲突（你的基线 #${expected}，当前最新 #${actual}），已自动刷新发布历史。`
      } else {
        saveConflictHint.value = '检测到新版本冲突，已自动刷新发布历史。'
      }
      return
    }
    closeSaveConfirmModal()
    return
  }
  saveEffectHint.value = restartKeys.length ? `已保存，需重启生效：${restartKeys.join(', ')}` : '已保存，立即生效'
  closeSaveConfirmModal()
  releaseNoteDraft.value = ''
  closeEditor()
}

async function retrySaveAfterConflict() {
  await confirmAndSaveSection()
}

async function resetSection(sectionId) {
  if (!sectionDirty(sectionId)) return
  props.platformSettingState.error = ''
  if (sectionId === 'pricing') {
    await props.saveRuntimeConfig({ api_model_pricing: getDefaultValue('api_model_pricing') || {} }, { note: `reset section:${sectionId}` })
    return
  }
  const patch = {}
  for (const def of sectionFields.value[sectionId] || []) patch[def.key] = getDefaultValue(def.key)
  await props.saveRuntimeConfig(patch, { note: `reset section:${sectionId}` })
}

function addPricingRow() {
  pricingDraftRows.value.push({ model: '', input_per_1k: 0, output_per_1k: 0 })
}

function removePricingRow(index) {
  pricingDraftRows.value.splice(index, 1)
}

const modalTitle = computed(() => sectionMeta.find((s) => s.id === activeSectionId.value)?.title || '参数设置')

function formatReleaseTime(ts) {
  const d = new Date(ts || '')
  if (Number.isNaN(d.getTime())) return '-'
  if (timeDisplayMode.value === 'exact') return d.toLocaleString()
  const diffMs = Date.now() - d.getTime()
  const sec = Math.max(1, Math.floor(diffMs / 1000))
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day < 30) return `${day} 天前`
  const month = Math.floor(day / 30)
  if (month < 12) return `${month} 个月前`
  const year = Math.floor(month / 12)
  return `${year} 年前`
}

async function refreshConfigHistory() {
  if (!props.loadConfigHistory) return
  historyLoading.value = true
  try {
    await props.loadConfigHistory(80)
  } finally {
    historyLoading.value = false
  }
}

async function refreshConfigAudit() {
  if (!props.loadConfigAudit) return
  auditLoading.value = true
  try {
    const prev = String(selectedAuditId.value || '')
    await props.loadConfigAudit({ ...auditFilters })
    const exists = (props.platformSettingState.configAuditItems || []).some((x) => String(x.audit_id) === prev)
    selectedAuditId.value = exists ? prev : ''
  } finally {
    auditLoading.value = false
  }
}

async function rollbackRelease(releaseId) {
  if (Number(releaseId) === 0) {
    if (!props.switchDefaultConfig) return
    if (!window.confirm('确认切换到默认版本吗？')) return
    await props.switchDefaultConfig()
  } else {
    if (!props.rollbackConfigRelease) return
    if (!window.confirm(`确认切换到版本 #${releaseId} 吗？`)) return
    await props.rollbackConfigRelease(releaseId)
  }
  await refreshConfigHistory()
}

async function deleteRelease(releaseId) {
  if (!props.deleteConfigRelease) return
  if (!window.confirm(`确认删除版本 #${releaseId} 吗？此操作不可恢复。`)) return
  await props.deleteConfigRelease(releaseId)
  if (String(selectedReleaseId.value) === String(releaseId)) {
    closeReleaseModal()
  }
}

function stableJson(v) {
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

const defaultRelease = computed(() => ({
  release_id: 0,
  status: 'DEFAULT',
  change_count: 0,
  created_by: 'system',
  created_at: '',
  note: '系统默认参数（不可删除）',
  snapshot_json: {
    ...RUNTIME_CONFIG_DEFAULTS,
    ...(props.platformSettingState.runtimeConfigDefaults || {}),
  },
}))

const releaseRows = computed(() => {
  const raw = Array.isArray(props.platformSettingState.configHistory) ? [...props.platformSettingState.configHistory] : []
  raw.sort((a, b) => Number(b?.release_id || 0) - Number(a?.release_id || 0))
  const rows = [defaultRelease.value, ...raw]
  const defaults = defaultRelease.value.snapshot_json || {}
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i]
    const snap = row?.snapshot_json && typeof row.snapshot_json === 'object' ? row.snapshot_json : {}
    const prev = i + 1 < rows.length ? (rows[i + 1]?.snapshot_json || {}) : defaults
    const keys = Array.from(new Set([...Object.keys(snap), ...Object.keys(prev), ...Object.keys(defaults)]))
    const changedFromPrev = keys.filter((k) => stableJson(snap[k]) !== stableJson(prev[k]))
    const changedFromDefault = keys.filter((k) => stableJson(snap[k]) !== stableJson(defaults[k]))
    const outdatedKeys = Object.keys(snap).filter((k) => !knownConfigKeys.value.has(k))
    row.__summary = {
      changed_from_prev: changedFromPrev.length,
      changed_from_default: changedFromDefault.length,
      outdated_keys: outdatedKeys.length,
      outdated_samples: outdatedKeys.slice(0, 5),
    }
  }
  return rows
})

const activeReleaseId = computed(() => {
  const current = props.platformSettingState.runtimeConfig || {}
  const rows = releaseRows.value
  for (const row of rows) {
    const snap = row?.snapshot_json && typeof row.snapshot_json === 'object' ? row.snapshot_json : {}
    const keys = Array.from(new Set([...Object.keys(snap), ...Object.keys(current)]))
    const same = keys.every((k) => stableJson(snap[k]) === stableJson(current[k]))
    if (same) return String(row.release_id)
  }
  return ''
})

const selectedRelease = computed(() => {
  const id = String(selectedReleaseId.value || '')
  return releaseRows.value.find((x) => String(x.release_id) === id) || null
})

const compareTargetRelease = computed(() => {
  const id = String(compareTargetReleaseId.value || '')
  return releaseRows.value.find((x) => String(x.release_id) === id) || null
})

const releaseModalDiffRows = computed(() => {
  const left = selectedRelease.value?.snapshot_json || {}
  const right = compareTargetRelease.value?.snapshot_json || {}
  const keys = Array.from(new Set([...Object.keys(left), ...Object.keys(right)])).sort((a, b) => a.localeCompare(b))
  return keys
    .map((key) => ({
      key,
      left: left[key],
      right: right[key],
      changed: stableJson(left[key]) !== stableJson(right[key]),
    }))
    .filter((x) => x.changed)
})

function openReleaseModal(releaseId) {
  selectedReleaseId.value = String(releaseId)
  compareTargetReleaseId.value = ''
  releaseModalTab.value = 'info'
  showReleaseModal.value = true
}

function closeReleaseModal() {
  showReleaseModal.value = false
}

function toggleTimeDisplayMode() {
  timeDisplayMode.value = timeDisplayMode.value === 'relative' ? 'exact' : 'relative'
}

onMounted(async () => {
  const cfg = props.platformSettingState.runtimeConfig
  const empty = !cfg || (typeof cfg === 'object' && Object.keys(cfg).length === 0)
  if (empty && !props.platformSettingState.loading) {
    await props.loadRuntimeConfig()
  }
  if (props.loadConfigHistory) {
    await refreshConfigHistory()
  }
  if (props.loadConfigAudit) {
    await refreshConfigAudit()
  }
})

watch(
  () => ({ ...draft }),
  () => {
    clearValidationUiOnEdit()
  },
  { deep: true },
)

watch(
  () => pricingDraftRows.value.map((x) => ({ ...x })),
  () => {
    clearValidationUiOnEdit()
  },
  { deep: true },
)

watch(
  () => selectedAuditId.value,
  () => {
    auditDiffKeyQuery.value = ''
    auditDiffKind.value = 'all'
    expandedAuditDiffKey.value = ''
  },
)
</script>

<template>
  <section class="content">
    <div class="panel">
      <div class="panel-title">平台参数</div>
      <p class="panel-sub">按功能板块管理运行参数</p>
      <p class="banner err" v-if="platformSettingState.error">{{ platformSettingState.error }}</p>
      <p class="banner ok" v-if="saveEffectHint">{{ saveEffectHint }}</p>
      <div class="row">
        <button class="btn ghost" :disabled="platformSettingState.loading" @click="loadRuntimeConfig">读取配置</button>
      </div>

      <section class="settings-section" :id="sectionDomId ? sectionDomId('config-release-history') : undefined" data-menu-section="config-release-history">
        <h2 class="section-title">配置发布历史</h2>
        <p class="section-tip">说明：`release_id` 为固定版本标识，不随列表排序变化。</p>
        <div class="row release-ops">
          <button class="btn ghost" :disabled="historyLoading || platformSettingState.loading" @click="refreshConfigHistory">刷新历史</button>
          <span class="release-op-tip">点击任一版本行可查看详情与对比</span>
        </div>

        <div class="release-list-wrap">
          <table class="release-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>状态</th>
                <th>变更项</th>
                <th>操作人</th>
                <th>
                  <button
                    type="button"
                    class="time-toggle"
                    :aria-pressed="timeDisplayMode === 'exact'"
                    :aria-label="timeDisplayMode === 'exact' ? '时间当前为精确时间，切换为相对时间' : '时间当前为相对时间，切换为精确时间'"
                    @click="toggleTimeDisplayMode"
                  >时间（{{ timeDisplayMode === 'exact' ? '精确' : '相对' }}）</button>
                </th>
                <th>备注</th>
                <th>摘要</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in releaseRows"
                :key="item.release_id"
                v-interactive-row="{ activate: () => openReleaseModal(item.release_id), label: `查看配置版本 ${item.release_id}`, current: String(item.release_id) === activeReleaseId }"
                class="release-row"
                :class="{ active: String(item.release_id) === activeReleaseId }"
                @click="openReleaseModal(item.release_id)"
              >
                <td>#{{ item.release_id }}</td>
                <td>
                  <div>{{ item.status || '-' }}</div>
                  <div class="subtle-text">应用：{{ item.apply_status || 'PENDING' }}</div>
                  <div v-if="item.apply_error" class="outdated-text" :title="item.apply_error">失败原因：{{ String(item.apply_error).slice(0, 36) }}{{ String(item.apply_error).length > 36 ? '...' : '' }}</div>
                </td>
                <td>{{ item.change_count || 0 }}</td>
                <td>{{ item.created_by || '-' }}</td>
                <td>{{ formatReleaseTime(item.created_at) }}</td>
                <td :title="item.note || ''">{{ item.note || '-' }}</td>
                <td>
                  <div>较上次 {{ item.__summary?.changed_from_prev ?? 0 }} 项</div>
                  <div>较默认 {{ item.__summary?.changed_from_default ?? 0 }} 项</div>
                  <div v-if="(item.__summary?.outdated_keys || 0) > 0" class="outdated-text" :title="(item.__summary?.outdated_samples || []).join(', ')">
                    过时字段 {{ item.__summary?.outdated_keys }}
                  </div>
                </td>
                <td>
                  <div class="release-row-actions">
                    <button class="btn ghost mini" :disabled="platformSettingState.loading" @click.stop="rollbackRelease(item.release_id)">切换到此版本</button>
                    <button class="btn ghost mini danger" :disabled="platformSettingState.loading || Number(item.release_id) === 0" @click.stop="deleteRelease(item.release_id)">删除版本</button>
                  </div>
                </td>
              </tr>
              <tr v-if="!releaseRows.length">
                <td colspan="8" class="release-empty" role="status" aria-live="polite" aria-atomic="true">暂无发布记录</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="settings-section" :id="sectionDomId ? sectionDomId('config-audit-timeline') : undefined" data-menu-section="config-audit-timeline">
        <h2 class="section-title">配置审计时间线</h2>
        <p class="section-tip">按配置项/操作人/动作/时间范围筛选配置变更记录。</p>
        <div class="grid three">
          <label>配置键<input v-model.trim="auditFilters.config_key" placeholder="如 memory_item_max" /></label>
          <label>操作人<input v-model.trim="auditFilters.actor_id" placeholder="如 admin" /></label>
          <label
            >动作
            <select v-model="auditFilters.action">
              <option value="">全部</option>
              <option value="PATCH_DRAFT">PATCH_DRAFT</option>
              <option value="PUBLISH">PUBLISH</option>
              <option value="ROLLBACK">ROLLBACK</option>
              <option value="SWITCH_DEFAULT">SWITCH_DEFAULT</option>
            </select>
          </label>
        </div>
        <div class="grid three">
          <label>开始时间<input v-model.trim="auditFilters.from_ts" type="datetime-local" /></label>
          <label>结束时间<input v-model.trim="auditFilters.to_ts" type="datetime-local" /></label>
          <label>条数上限<input v-model.number="auditFilters.limit" type="number" min="1" max="2000" step="1" /></label>
        </div>
        <div class="grid three">
          <label>发布版本 ID<input v-model.number="auditFilters.release_id" type="number" min="0" step="1" placeholder="0=全部" /></label>
          <label>Trace ID<input v-model.trim="auditFilters.trace_id" placeholder="按 trace_id 精确筛选" /></label>
          <label></label>
        </div>
        <div class="row release-ops">
          <button class="btn ghost" :disabled="auditLoading" @click="refreshConfigAudit">刷新审计</button>
          <span class="release-op-tip">共 {{ (platformSettingState.configAuditItems || []).length }} 条</span>
        </div>
        <div class="release-list-wrap">
          <table class="release-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>动作</th>
                <th>发布版本</th>
                <th>配置键</th>
                <th>操作人</th>
                <th>旧值</th>
                <th>新值</th>
                <th>trace_id</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in platformSettingState.configAuditItems || []"
                :key="`audit-${row.audit_id}`"
                v-interactive-row="{ activate: () => { selectedAuditId = String(row.audit_id) }, label: `查看配置审计 ${row.action || ''} ${row.config_key || ''}`, current: String(row.audit_id) === String(selectedAuditId || '') }"
                class="release-row"
                :class="{ active: String(row.audit_id) === String(selectedAuditId || '') }"
                @click="selectedAuditId = String(row.audit_id)"
              >
                <td>{{ formatReleaseTime(row.created_at) }}</td>
                <td>{{ row.action || '-' }}</td>
                <td>{{ Number(row.release_id || 0) > 0 ? `#${row.release_id}` : '-' }}</td>
                <td>{{ row.config_key || '-' }}</td>
                <td>{{ row.actor_id || '-' }}</td>
                <td :title="JSON.stringify(row.old_json)">{{ String(JSON.stringify(row.old_json)).slice(0, 80) }}</td>
                <td :title="JSON.stringify(row.new_json)">{{ String(JSON.stringify(row.new_json)).slice(0, 80) }}</td>
                <td :title="row.trace_id || ''">{{ String(row.trace_id || '-').slice(0, 24) }}</td>
              </tr>
              <tr v-if="!(platformSettingState.configAuditItems || []).length">
                <td colspan="8" class="release-empty" role="status" aria-live="polite" aria-atomic="true">暂无审计记录</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="diff-wrap" v-if="selectedAuditRow">
          <div class="diff-head">
            <b>审计差异</b>
            <span>#{{ selectedAuditRow.audit_id }} / {{ selectedAuditRow.action }} / {{ selectedAuditRow.config_key }}</span>
            <span v-if="selectedAuditDiffRows.length">共 {{ selectedAuditDiffRows.length }} 项</span>
            <span v-if="filteredAuditDiffRows.length !== selectedAuditDiffRows.length">（筛选后 {{ filteredAuditDiffRows.length }} 项）</span>
          </div>
          <div class="diff-filter-row">
            <input v-model.trim="auditDiffKeyQuery" type="text" placeholder="按键名过滤（如 memory_）" />
            <select v-model="auditDiffKind">
              <option value="all">全部类型</option>
              <option value="added">仅新增</option>
              <option value="removed">仅删除</option>
              <option value="modified">仅修改</option>
            </select>
          </div>
          <div class="diff-list">
            <div class="diff-row expandable" v-for="it in filteredAuditDiffRows" :key="`audit-diff-${it.key}`">
              <div class="diff-row-head" @click="toggleAuditDiffExpand(it.key)">
                <div class="diff-key">{{ it.key }}</div>
                <div class="diff-row-meta">
                  <span class="diff-kind" :class="`kind-${it.kind}`">{{ diffKindLabel(it.kind) }}</span>
                  <span class="diff-expand">{{ expandedAuditDiffKey === it.key ? '收起' : '展开' }}</span>
                </div>
              </div>
              <template v-if="expandedAuditDiffKey === it.key">
                <div class="diff-split">
                  <div class="diff-pane">
                    <div class="diff-pane-head">旧值</div>
                    <pre class="diff-pane-body">{{ formatJsonPretty(it.oldVal) }}</pre>
                  </div>
                  <div class="diff-pane">
                    <div class="diff-pane-head">新值</div>
                    <pre class="diff-pane-body">{{ formatJsonPretty(it.newVal) }}</pre>
                  </div>
                </div>
              </template>
              <template v-else>
                <div class="diff-val diff-del">- {{ JSON.stringify(it.oldVal) }}</div>
                <div class="diff-val diff-add">+ {{ JSON.stringify(it.newVal) }}</div>
              </template>
            </div>
            <div v-if="selectedAuditDiffRows.length && !filteredAuditDiffRows.length" class="diff-empty">没有匹配筛选条件的差异项</div>
            <div v-else-if="!selectedAuditDiffRows.length" class="diff-empty">无差异（或该操作不包含键值更新）</div>
          </div>
        </div>
      </section>

      <section
        v-for="section in sectionMeta"
        :key="section.id"
        class="settings-section"
        :id="sectionDomId ? sectionDomId(section.id) : undefined"
        :data-menu-section="section.id"
      >
        <h2 class="section-title">{{ section.title }}</h2>

        <div class="kv-grid" v-if="section.id !== 'pricing'">
          <div class="kv-row" v-for="def in sectionFields[section.id]" :key="def.key">
            <div class="k">
              {{ def.label }}
              <span class="field-badge hot" v-if="isFieldHotReload(def)">热更新</span>
              <span class="field-badge restart" v-else>需重启</span>
              <span class="field-badge non-versioned" v-if="!isFieldVersioned(def)">非版本化</span>
              <span class="help-dot" :title="def.help">?</span>
              <span v-if="fieldError(def)" class="field-error-badge" :title="fieldError(def)">错误</span>
            </div>
            <div class="v">{{ formatDisplay(def, getCurrentValue(def.key)) }}</div>
          </div>
        </div>

        <div class="kv-grid" v-else>
          <div class="kv-row">
            <div class="k">
              定价规则数量
              <span class="help-dot" title="按模型维护 input/output 每千 token 单价。">?</span>
            </div>
            <div class="v">{{ Object.keys(getCurrentValue('api_model_pricing') || {}).length }}</div>
          </div>
          <div class="kv-row">
            <div class="k">默认模型价格</div>
            <div class="v">{{ formatDisplay({ type: 'text' }, JSON.stringify((getCurrentValue('api_model_pricing') || {}).default || {})) }}</div>
          </div>
        </div>

        <div class="section-actions">
          <button class="btn ghost" :disabled="platformSettingState.loading || !sectionDirty(section.id)" @click="resetSection(section.id)">重置默认</button>
          <button class="btn primary" :disabled="platformSettingState.loading" @click="openSectionEditor(section.id)">编辑</button>
        </div>
      </section>
    </div>

    <transition name="modal">
      <div v-if="showModal" v-modal-focus="{ close: closeEditor, label: `${modalTitle}参数设置` }" class="modal-mask">
        <div class="modal-card">
          <div class="modal-head">
            <div class="modal-title">{{ modalTitle }}参数设置</div>
            <button type="button" class="icon-close" :aria-label="`关闭${modalTitle}参数设置`" @click="closeEditor"><span aria-hidden="true">×</span></button>
          </div>

          <div class="modal-body" v-if="activeSectionId !== 'pricing'">
            <div class="grid three">
              <label class="field-wrap" v-for="def in sectionFields[activeSectionId] || []" :key="def.key">
                <span class="label-row">
                  <span>{{ def.label }}</span>
                  <span class="field-badge hot" v-if="isFieldHotReload(def)">热更新</span>
                  <span class="field-badge restart" v-else>需重启</span>
                  <span class="field-badge non-versioned" v-if="!isFieldVersioned(def)">非版本化</span>
                  <span class="help-dot" :title="def.help">?</span>
                  <span v-if="fieldError(def)" class="field-error-badge" :title="fieldError(def)">错误</span>
                </span>
                <input
                  v-if="def.type === 'text' || def.type === 'password'"
                  v-model="draft[def.key]"
                  :type="def.type === 'password' ? 'password' : 'text'"
                  :class="{ 'invalid-input': !!fieldError(def) }"
                />
                <input
                  v-else-if="def.type === 'number'"
                  v-model.number="draft[def.key]"
                  type="number"
                  :min="getValidationMeta(def).min ?? undefined"
                  :max="getValidationMeta(def).max ?? undefined"
                  :step="def.step || getValidationMeta(def).step || 1"
                  :class="{ 'invalid-input': !!fieldError(def) }"
                />
                <select v-else-if="def.type === 'boolean'" v-model="draft[def.key]" :class="{ 'invalid-input': !!fieldError(def) }">
                  <option :value="true">开启</option>
                  <option :value="false">关闭</option>
                </select>
                <select v-else-if="def.type === 'select'" v-model="draft[def.key]" :class="{ 'invalid-input': !!fieldError(def) }">
                  <option v-for="opt in (getValidationMeta(def).enum || def.options || [])" :key="opt" :value="opt">{{ opt }}</option>
                </select>
                <button
                  v-else-if="def.type === 'textarea'"
                  type="button"
                  class="longtext-trigger"
                  :class="{ 'invalid-input': !!fieldError(def) }"
                  :title="draft[def.key] || '点击编辑长文本'"
                  @click="openLongTextEditor(def)"
                >
                  {{ draft[def.key] ? String(draft[def.key]).slice(0, 90) : '点击编辑长文本' }}
                </button>
              </label>
            </div>
          </div>

          <div class="modal-body" v-else>
            <div class="pricing-list">
              <div class="pricing-row" v-for="(row, idx) in pricingDraftRows" :key="idx">
                <input v-model="row.model" placeholder="模型名，如 deepseek-ai/DeepSeek-V3.2" />
                <input v-model.number="row.input_per_1k" type="number" min="0" step="0.0001" placeholder="input_per_1k" />
                <input v-model.number="row.output_per_1k" type="number" min="0" step="0.0001" placeholder="output_per_1k" />
                <button class="btn ghost" @click="removePricingRow(idx)">删除</button>
              </div>
            </div>
            <div class="row">
              <button class="btn ghost" @click="addPricingRow">新增模型定价</button>
            </div>
          </div>

          <div class="modal-foot">
            <div class="save-impact">
              <div class="save-impact-title">本次改动预览</div>
              <div class="save-impact-stats">
                <span>变更 {{ saveImpactSummary.total }} 项</span>
                <span>热更新 {{ saveImpactSummary.hot }}</span>
                <span>需重启 {{ saveImpactSummary.restart }}</span>
                <span>版本化 {{ saveImpactSummary.versioned }}</span>
                <span>非版本化 {{ saveImpactSummary.nonVersioned }}</span>
              </div>
              <div class="save-impact-keys" :title="pendingPatchKeys.join(', ')">
                {{ pendingPatchKeys.length ? pendingPatchKeys.join('，') : '暂无变更' }}
              </div>
            </div>
            <input v-model="releaseNoteDraft" class="release-note-input" type="text" maxlength="140" placeholder="发布备注（可选）" />
            <button class="btn primary" :disabled="platformSettingState.loading || !pendingPatchKeys.length" @click="saveSection">保存</button>
            <button class="btn ghost" @click="closeEditor">取消</button>
          </div>
        </div>
      </div>
    </transition>

    <transition name="modal">
      <div v-if="showSaveConfirmModal" v-modal-focus="{ close: closeSaveConfirmModal, label: '确认保存本次改动' }" class="modal-mask inner-mask">
        <div class="modal-card confirm-card">
          <div class="modal-head">
            <div class="modal-title">确认保存本次改动</div>
            <button type="button" class="icon-close" aria-label="关闭保存确认" @click="closeSaveConfirmModal"><span aria-hidden="true">×</span></button>
          </div>
          <div class="modal-body">
            <div class="confirm-stats">
              <span>变更 {{ saveImpactSummary.total }} 项</span>
              <span>热更新 {{ saveImpactSummary.hot }}</span>
              <span>需重启 {{ saveImpactSummary.restart }}</span>
              <span>版本化 {{ saveImpactSummary.versioned }}</span>
              <span>非版本化 {{ saveImpactSummary.nonVersioned }}</span>
            </div>
            <div class="confirm-impact" v-if="impactLoading">影响面分析中...</div>
            <div class="confirm-impact err" v-else-if="impactError">{{ impactError }}</div>
            <div class="confirm-impact" v-else-if="impactPreview?.impact">
              <span>风险等级：{{ riskLevelLabel(impactPreview.impact.risk_level) }}</span>
              <span v-if="impactPreview.impact.impact_services?.length">影响服务：{{ impactPreview.impact.impact_services.join(' / ') }}</span>
              <span v-if="impactPreview.impact.impact_modules?.length">影响模块：{{ impactPreview.impact.impact_modules.join(' / ') }}</span>
              <span v-if="impactPreview.impact.reasons?.length">说明：{{ impactPreview.impact.reasons.join('；') }}</span>
            </div>
            <div class="confirm-impact warn" v-if="saveConflictDetected">{{ saveConflictHint }}</div>
            <div class="confirm-impact warn" v-if="saveConflictDetected && saveConflictDiffRows.length">
              <span>冲突差异（当前编辑值 vs 最新发布值）共 {{ saveConflictDiffRows.length }} 项：</span>
              <div class="conflict-diff-list">
                <div class="conflict-diff-row" v-for="it in saveConflictDiffRows" :key="`conflict-${it.key}`">
                  <div class="conflict-diff-key">{{ it.key }}</div>
                  <div class="conflict-diff-val">
                    <b>最新：</b>{{ formatJsonPretty(it.latest) }}
                  </div>
                  <div class="conflict-diff-val">
                    <b>当前：</b>{{ formatJsonPretty(it.draft) }}
                  </div>
                </div>
              </div>
            </div>
            <pre class="confirm-text">{{ confirmSummaryText }}</pre>
            <div class="row">
              <button class="btn ghost mini" @click="copyConfirmSummary">复制变更清单</button>
              <span class="confirm-copy-hint" v-if="confirmCopyHint">{{ confirmCopyHint }}</span>
            </div>
          </div>
          <div class="modal-foot">
            <button class="btn ghost" @click="closeSaveConfirmModal">取消</button>
            <button class="btn ghost" v-if="saveConflictDetected" :disabled="platformSettingState.loading" @click="retrySaveAfterConflict">基线刷新后重试</button>
            <button class="btn primary" :disabled="platformSettingState.loading" @click="confirmAndSaveSection">确认保存</button>
          </div>
        </div>
      </div>
    </transition>

    <transition name="modal">
      <div v-if="showLongTextModal" v-modal-focus="{ close: closeLongTextEditor, label: longTextFieldLabel }" class="modal-mask inner-mask">
        <div class="modal-card longtext-card">
          <div class="modal-head">
            <div class="modal-title">{{ longTextFieldLabel }}</div>
            <button type="button" class="icon-close" :aria-label="`关闭${longTextFieldLabel}`" @click="closeLongTextEditor"><span aria-hidden="true">×</span></button>
          </div>
          <div class="modal-body">
            <textarea v-model="longTextDraft" class="longtext-editor" />
          </div>
          <div class="modal-foot">
            <button class="btn ghost" @click="closeLongTextEditor">取消</button>
            <button class="btn primary" @click="applyLongTextEditor">应用</button>
          </div>
        </div>
      </div>
    </transition>

    <transition name="modal">
      <div v-if="showReleaseModal" v-modal-focus="{ close: closeReleaseModal, label: `版本 ${selectedRelease?.release_id || ''} 详情` }" class="modal-mask">
        <div class="modal-card release-modal">
          <div class="modal-head">
            <div class="modal-title">版本 #{{ selectedRelease?.release_id }} 详情</div>
            <button type="button" class="icon-close" aria-label="关闭版本详情" @click="closeReleaseModal"><span aria-hidden="true">×</span></button>
          </div>
          <div class="release-tabs">
            <button class="btn ghost mini" :class="{ active: releaseModalTab === 'info' }" @click="releaseModalTab = 'info'">版本信息</button>
            <button class="btn ghost mini" :class="{ active: releaseModalTab === 'diff' }" @click="releaseModalTab = 'diff'">版本对比</button>
          </div>
          <div class="modal-body" v-if="releaseModalTab === 'info'">
            <div class="release-info-grid">
              <div><b>ID</b><span>#{{ selectedRelease?.release_id }}</span></div>
              <div><b>状态</b><span>{{ selectedRelease?.status || '-' }}</span></div>
              <div><b>操作人</b><span>{{ selectedRelease?.created_by || '-' }}</span></div>
              <div><b>时间</b><span>{{ formatReleaseTime(selectedRelease?.created_at) }}</span></div>
              <div class="wide"><b>备注</b><span>{{ selectedRelease?.note || '-' }}</span></div>
              <div class="wide"><b>摘要</b><span>较上次 {{ selectedRelease?.__summary?.changed_from_prev ?? 0 }} 项；较默认 {{ selectedRelease?.__summary?.changed_from_default ?? 0 }} 项；过时字段 {{ selectedRelease?.__summary?.outdated_keys ?? 0 }} 项</span></div>
            </div>
            <pre class="release-json">{{ JSON.stringify(selectedRelease?.snapshot_json || {}, null, 2) }}</pre>
          </div>
          <div class="modal-body" v-else>
            <div class="row release-compare-row">
              <span>左侧版本：#{{ selectedRelease?.release_id }}</span>
              <select v-model="compareTargetReleaseId">
                <option value="">选择右侧对比版本</option>
                <option v-for="item in releaseRows.filter((x) => String(x.release_id) !== String(selectedRelease?.release_id || ''))" :key="`cmp-${item.release_id}`" :value="item.release_id">
                  #{{ item.release_id }} {{ item.status }}
                </option>
              </select>
            </div>
            <div class="split-diff" v-if="compareTargetRelease">
              <div class="split-pane">
                <div class="split-head">#{{ selectedRelease?.release_id }}</div>
                <pre>{{ JSON.stringify(selectedRelease?.snapshot_json || {}, null, 2) }}</pre>
              </div>
              <div class="split-pane">
                <div class="split-head">#{{ compareTargetRelease?.release_id }}</div>
                <pre>{{ JSON.stringify(compareTargetRelease?.snapshot_json || {}, null, 2) }}</pre>
              </div>
            </div>
            <div class="diff-wrap">
              <div class="diff-head"><b>逐项差异</b><span v-if="compareTargetRelease">共 {{ releaseModalDiffRows.length }} 项</span></div>
              <div class="diff-list">
                <div class="diff-row" v-for="it in releaseModalDiffRows" :key="`modal-${it.key}`">
                  <div class="diff-key">{{ it.key }}</div>
                  <div class="diff-val diff-del">- {{ JSON.stringify(it.left) }}</div>
                  <div class="diff-val diff-add">+ {{ JSON.stringify(it.right) }}</div>
                </div>
                <div v-if="compareTargetRelease && !releaseModalDiffRows.length" class="diff-empty">无差异</div>
                <div v-if="!compareTargetRelease" class="diff-empty">请选择右侧对比版本</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </section>
</template>

<style scoped>
.settings-section {
  margin-top: 26px;
  padding-top: 8px;
}

.section-title {
  margin: 0 0 14px;
  font-size: 22px;
  font-weight: 800;
  color: #213956;
}

.section-tip {
  margin: -4px 0 10px;
  color: #5a6f88;
  font-size: 13px;
}

.banner.ok {
  margin-top: 8px;
  border: 1px solid #bfe8cf;
  background: #effcf4;
  color: #1f6b3f;
}

.kv-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.kv-row {
  display: grid;
  gap: 8px;
  border: 1px solid #e4ecf7;
  border-radius: 10px;
  padding: 10px 12px;
  background: #fbfdff;
}

.k {
  color: #425a78;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.field-badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.field-badge.hot {
  color: #1f6b3f;
  background: #eaf8ef;
  border: 1px solid #bfe6cb;
}

.field-badge.restart {
  color: #9a6a18;
  background: #fff7ea;
  border: 1px solid #f1d59c;
}

.field-badge.non-versioned {
  color: #3b4f68;
  background: #eef2f8;
  border: 1px solid #cfd8e6;
}

.field-error-badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  color: #b13e3e;
  background: #fff1f1;
  border: 1px solid #f0baba;
  cursor: help;
}

.v {
  color: #1f2f44;
  font-weight: 600;
  word-break: break-word;
}

.help-dot {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  border: 1px solid #c6d7ec;
  background: #eef5ff;
  color: #35577d;
  font-size: 12px;
  line-height: 1;
  display: inline-grid;
  place-items: center;
  cursor: help;
}

.section-actions {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.release-ops {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.release-op-tip {
  color: #5a6f88;
  font-size: 13px;
}

.release-list-wrap {
  margin-top: 12px;
  border: 1px solid #dce7f5;
  border-radius: 10px;
  background: #fff;
  overflow: auto;
  max-height: 420px;
}

.release-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 920px;
}

.release-table th,
.release-table td {
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid #edf2fa;
  font-size: 13px;
  color: #27435f;
}

.release-table th {
  background: #f6f9ff;
  font-weight: 700;
  color: #23405e;
}

.release-row {
  cursor: pointer;
}

.release-row-actions {
  display: inline-flex;
  gap: 6px;
}

.btn.danger {
  border-color: #e5b8b8;
  color: #a54545;
}

.btn.danger:hover {
  border-color: #d98e8e;
  color: #8e2d2d;
  background: #fff6f6;
}

.release-row:hover td {
  background: #f7fbff;
}

.release-row.active td {
  background: #eef6ff;
}

.outdated-text {
  color: #b36a10;
  font-weight: 700;
}

.subtle-text {
  color: #5f7390;
  font-size: 12px;
}

.time-toggle {
  appearance: none;
  border: 0;
  padding: 2px 0;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: inherit;
  cursor: pointer;
  user-select: none;
}

.time-toggle:focus-visible {
  outline: 2px solid #2563eb;
  outline-offset: 3px;
  border-radius: 4px;
}

.release-empty {
  text-align: center !important;
  color: #6f839b !important;
}

.diff-wrap {
  margin-top: 12px;
  border: 1px solid #dce7f5;
  border-radius: 10px;
  background: #fbfdff;
}

.diff-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid #e8effa;
  color: #2a4766;
}

.diff-list {
  max-height: 280px;
  overflow: auto;
}

.diff-filter-row {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) 180px;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #e8effa;
  background: #fff;
}

.diff-row {
  padding: 10px 12px;
  border-bottom: 1px dashed #e7eef9;
  display: grid;
  gap: 4px;
}

.diff-row.expandable {
  gap: 8px;
}

.diff-row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  cursor: pointer;
}

.diff-row-meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.diff-kind {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.diff-kind.kind-added {
  color: #1d6b3f;
  background: #eaf8ef;
  border: 1px solid #bfe6cb;
}

.diff-kind.kind-removed {
  color: #a13f3f;
  background: #fff1f1;
  border: 1px solid #f0baba;
}

.diff-kind.kind-modified {
  color: #7a5b16;
  background: #fff8e9;
  border: 1px solid #f0ddad;
}

.diff-expand {
  font-size: 12px;
  color: #486687;
}

.diff-split {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.diff-pane {
  border: 1px solid #dce7f6;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}

.diff-pane-head {
  padding: 6px 8px;
  font-size: 12px;
  font-weight: 700;
  color: #2c4a68;
  background: #f6f9ff;
  border-bottom: 1px solid #e5edf9;
}

.diff-pane-body {
  margin: 0;
  padding: 8px;
  max-height: 220px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.45;
  color: #35516e;
  white-space: pre-wrap;
  word-break: break-word;
}

.diff-key {
  font-weight: 700;
  color: #1f3a58;
}

.diff-val {
  color: #35516e;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  white-space: pre-wrap;
  word-break: break-word;
}

.diff-del {
  background: #fff2f3;
  border-left: 3px solid #d84545;
  padding: 6px 8px;
}

.diff-add {
  background: #effbf3;
  border-left: 3px solid #1e8a4e;
  padding: 6px 8px;
}

.diff-empty {
  padding: 10px 12px;
  color: #6f839b;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(19, 28, 40, 0.35);
  display: grid;
  place-items: center;
  z-index: 999;
  padding: 18px;
}

.modal-card {
  width: min(1060px, 96vw);
  max-height: 88vh;
  overflow: auto;
  background: #fff;
  border-radius: 12px;
  border: 1px solid #d8e5f5;
  box-shadow: 0 20px 48px rgba(20, 32, 48, 0.2);
}

.modal-head {
  position: sticky;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #e5edf8;
  background: #fff;
}

.modal-title {
  font-size: 18px;
  font-weight: 700;
}

.icon-close {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  border: 1px solid #ccdaee;
  background: #f7faff;
  cursor: pointer;
}

.modal-body {
  padding: 14px;
}

.field-wrap {
  min-width: 0;
  display: grid;
  gap: 6px;
}

.label-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.invalid-input {
  border-color: #de6b6b !important;
  box-shadow: 0 0 0 1px rgba(222, 107, 107, 0.2) inset;
}

.pricing-list {
  display: grid;
  gap: 8px;
}

.pricing-row {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) 170px 170px auto;
  gap: 8px;
  align-items: center;
}

.modal-foot {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 14px;
  border-top: 1px solid #e5edf8;
  background: #fff;
}

.save-impact {
  margin-right: auto;
  min-width: 300px;
  max-width: min(760px, 100%);
  border: 1px solid #dbe7f8;
  border-radius: 10px;
  background: #f8fbff;
  padding: 8px 10px;
}

.save-impact-title {
  font-size: 12px;
  color: #446282;
  font-weight: 700;
}

.save-impact-stats {
  margin-top: 4px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: #2f4f70;
}

.save-impact-keys {
  margin-top: 6px;
  font-size: 12px;
  color: #375878;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.release-note-input {
  min-width: 320px;
}

.longtext-trigger {
  width: 100%;
  min-height: 40px;
  border: 1px solid #c8d7eb;
  border-radius: 10px;
  padding: 8px 10px;
  background: #fff;
  text-align: left;
  color: #243245;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}

.confirm-card {
  width: min(760px, 92vw);
}

.confirm-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 13px;
  color: #35506f;
}

.confirm-impact {
  margin-top: 8px;
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: #35506f;
  border: 1px solid #dce7f6;
  border-radius: 8px;
  background: #f8fbff;
  padding: 8px 10px;
}

.confirm-impact.err {
  color: #a13f3f;
  border-color: #f0c0c0;
  background: #fff4f4;
}

.confirm-impact.warn {
  color: #8f6415;
  border-color: #f4db9a;
  background: #fff9ea;
}

.conflict-diff-list {
  margin-top: 6px;
  max-height: 180px;
  overflow: auto;
  border: 1px solid #ecd8a8;
  border-radius: 8px;
  background: #fffdf7;
}

.conflict-diff-row {
  padding: 8px 10px;
  border-bottom: 1px solid #f2e7c8;
}

.conflict-diff-row:last-child {
  border-bottom: none;
}

.conflict-diff-key {
  font-weight: 700;
  color: #6d4b0d;
}

.conflict-diff-val {
  margin-top: 2px;
  color: #6c5a35;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.confirm-text {
  margin-top: 10px;
  border: 1px solid #dce7f6;
  border-radius: 10px;
  padding: 10px 12px;
  background: #f8fbff;
  color: #24415f;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.confirm-copy-hint {
  font-size: 12px;
  color: #446282;
}

.inner-mask {
  background: rgba(19, 28, 40, 0.48);
}

.longtext-card {
  width: min(980px, 94vw);
}

.longtext-editor {
  width: 100%;
  height: 46vh;
  min-height: 320px;
  resize: none;
}

.release-modal {
  width: min(1180px, 96vw);
}

.release-tabs {
  display: flex;
  gap: 8px;
  padding: 10px 14px 0;
}

.release-tabs .btn.active {
  border-color: #7ea6d4;
  color: #183c63;
  background: #eef6ff;
}

.release-info-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
}

.release-info-grid > div {
  border: 1px solid #e4ecf7;
  border-radius: 8px;
  padding: 8px 10px;
  display: grid;
  gap: 4px;
}

.release-info-grid > div.wide {
  grid-column: 1 / -1;
}

.release-info-grid b {
  color: #445e79;
  font-size: 12px;
}

.release-json {
  margin-top: 10px;
  max-height: 280px;
  overflow: auto;
  border: 1px solid #dce7f5;
  border-radius: 8px;
  padding: 10px;
  background: #fcfeff;
}

.release-compare-row {
  align-items: center;
}

.split-diff {
  margin-top: 10px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.split-pane {
  border: 1px solid #dce7f5;
  border-radius: 8px;
  overflow: auto;
}

.split-head {
  padding: 8px 10px;
  border-bottom: 1px solid #e7eef9;
  background: #f7fbff;
  font-weight: 700;
}

.split-pane pre {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 10px;
  background: #fff;
}

.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.18s ease;
}

.modal-enter-active .modal-card,
.modal-leave-active .modal-card {
  transition: transform 0.2s ease, opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from .modal-card,
.modal-leave-to .modal-card {
  transform: translateY(14px) scale(0.98);
  opacity: 0;
}

@media (max-width: 1320px) {
  .pricing-row {
    grid-template-columns: 1fr;
  }

  .release-ops {
    align-items: flex-start;
    flex-direction: column;
  }

  .split-diff {
    grid-template-columns: 1fr;
  }

  .release-note-input {
    min-width: 0;
    width: 100%;
  }
}
</style>
