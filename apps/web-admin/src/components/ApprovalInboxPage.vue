<script setup>
import { computed, onMounted } from 'vue'
import { useApprovalAdmin } from '../composables/useApprovalAdmin'

const props = defineProps({
  fetchJson: { type: Function, required: true },
  sectionDomId: { type: Function, required: true },
})

const approvals = useApprovalAdmin(props.fetchJson)
const state = approvals.state

const decisionTitle = computed(() => state.decisionDraft === 'approve' ? '确认同意' : '确认拒绝')
const trendMax = computed(() => Math.max(1, ...state.trend.flatMap((item) => [item.created_count || 0, item.decided_count || 0])))
const trendTotals = computed(() => state.trend.reduce((total, item) => ({
  created: total.created + (Number(item.created_count) || 0),
  decided: total.decided + (Number(item.decided_count) || 0),
  timedOut: total.timedOut + (Number(item.timed_out_count) || 0),
}), { created: 0, decided: 0, timedOut: 0 }))

function when(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

function statusLabel(status) {
  return { PENDING: '待审批', DECIDED: '已处理', CANCELLED: '已取消' }[status] || status || '未知'
}

function decisionLabel(item) {
  if (item?.timed_out) return `超时${item.decision === 'approve' ? '同意' : '拒绝'}`
  return item?.decision === 'approve' ? '已同意' : item?.decision === 'reject' ? '已拒绝' : '—'
}

function healthLabel(health) {
  return { HEALTHY: '正常', WARN: '需关注', CRITICAL: '严重' }[health] || '暂无数据'
}

function durationLabel(seconds) {
  if (seconds == null || seconds === '') return '—'
  const value = Math.max(0, Number(seconds) || 0)
  if (value < 60) return `${Math.round(value)} 秒`
  if (value < 3600) return `${Math.round(value / 60)} 分钟`
  if (value < 86400) return `${(value / 3600).toFixed(value < 36000 ? 1 : 0)} 小时`
  return `${(value / 86400).toFixed(1)} 天`
}

function percentLabel(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`
}

function alertTypeLabel(value) {
  return { DUE_SOON: '即将到期', OVERDUE: '已逾期', TIMED_OUT: '已超时' }[value] || value || '未知告警'
}

function alertStatusLabel(value) {
  return { OPEN: '待响应', ACKNOWLEDGED: '已确认', RESOLVED: '已解决' }[value] || value || '未知'
}

function deliveryStatusLabel(value) {
  return {
    DISABLED: '未配置', PENDING: '待投递', IN_FLIGHT: '投递中', RETRY: '等待重试',
    PUBLISHED: '已送达', DEAD_LETTER: '投递死信',
  }[value] || value || '无投递'
}

function shortDate(value) {
  const parts = String(value || '').split('-')
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : value || '—'
}

function barHeight(value) {
  const count = Number(value) || 0
  return count ? `${Math.max(5, (count / trendMax.value) * 100)}%` : '0%'
}

async function safely(action) {
  try { await action() } catch { /* composable exposes actionable errors */ }
}

onMounted(() => safely(approvals.load))
</script>

<template>
  <section class="approval-page">
    <section class="domain-section" :id="sectionDomId('queue')" data-menu-section="queue">
      <div class="approval-heading">
        <div>
          <div class="eyebrow">Human in the loop</div>
          <h2 class="domain-section-title">审批待办</h2>
          <p class="section-copy">审批状态和执行检查点由 Control Plane 持久化。页面关闭或 Worker 重启不会丢失待办。</p>
        </div>
        <div class="heading-actions">
          <label class="status-filter">状态
            <select v-model="state.status" :disabled="state.loading" @change="safely(approvals.load)">
              <option value="PENDING">待审批</option>
              <option value="DECIDED">已处理</option>
              <option value="CANCELLED">已取消</option>
              <option value="">全部</option>
            </select>
          </label>
          <label class="status-filter">临期窗口
            <select v-model.number="state.dueWithinSeconds" :disabled="state.loading" @change="safely(approvals.load)">
              <option :value="900">15 分钟</option>
              <option :value="1800">30 分钟</option>
              <option :value="3600">1 小时</option>
              <option :value="14400">4 小时</option>
              <option :value="86400">24 小时</option>
            </select>
          </label>
          <button class="btn ghost" :disabled="state.loading" @click="safely(approvals.load)">
            {{ state.loading ? '刷新中…' : '刷新' }}
          </button>
        </div>
      </div>

      <p v-if="state.error" class="banner err" role="alert">{{ state.error }}</p>
      <p v-if="state.message" class="banner ok" role="status">{{ state.message }}</p>

      <div class="sla-grid" aria-label="审批运营摘要">
        <article :class="['sla-card', String(state.stats?.health || '').toLowerCase()]">
          <span>队列健康</span><strong>{{ healthLabel(state.stats?.health) }}</strong>
          <small>按当前用户可处理范围统计</small>
        </article>
        <article class="sla-card">
          <span>待处理</span><strong>{{ state.stats?.pending_count ?? '—' }}</strong>
          <small>最老已等待 {{ durationLabel(state.stats?.oldest_pending_age_seconds) }}</small>
        </article>
        <article :class="['sla-card', { critical: (state.stats?.overdue_count || 0) > 0 }]">
          <span>临期 / 逾期</span><strong>{{ state.stats?.due_soon_count ?? '—' }} / {{ state.stats?.overdue_count ?? '—' }}</strong>
          <small>临期阈值 {{ durationLabel(state.stats?.due_within_seconds || state.dueWithinSeconds) }}</small>
        </article>
        <article class="sla-card">
          <span>24h 超时率</span><strong>{{ percentLabel(state.stats?.timeout_rate_24h) }}</strong>
          <small>{{ state.stats?.timed_out_count_24h || 0 }} / {{ state.stats?.decided_count_24h || 0 }} 项</small>
        </article>
        <article class="sla-card">
          <span>24h 平均处理</span><strong>{{ durationLabel(state.stats?.average_decision_seconds_24h) }}</strong>
          <small>仅统计已做出决定的审批</small>
        </article>
      </div>

      <section class="trend-panel" aria-labelledby="approval-trend-title">
        <div class="trend-heading">
          <div>
            <div class="eyebrow">Retained audit facts · UTC</div>
            <h3 id="approval-trend-title">审批处理趋势</h3>
            <p>历史事实独立于 Run 保留；删除会话后仍可用于超时率与处理效率审计。</p>
          </div>
          <label class="status-filter">趋势窗口
            <select v-model.number="state.trendDays" :disabled="state.loading" @change="safely(approvals.load)">
              <option :value="7">7 天</option>
              <option :value="14">14 天</option>
              <option :value="30">30 天</option>
              <option :value="90">90 天</option>
            </select>
          </label>
        </div>
        <div class="trend-summary" aria-label="趋势汇总">
          <span><strong>{{ trendTotals.created }}</strong> 新建</span>
          <span><strong>{{ trendTotals.decided }}</strong> 已决定</span>
          <span :class="{ risk: trendTotals.timedOut > 0 }"><strong>{{ trendTotals.timedOut }}</strong> 超时</span>
        </div>
        <div class="trend-chart" :aria-label="`最近 ${state.trendDays} 天审批趋势`">
          <div
            v-for="(point, index) in state.trend"
            :key="point.date"
            class="trend-day"
            :title="`${point.date}：新建 ${point.created_count}，决定 ${point.decided_count}，超时 ${point.timed_out_count}`"
          >
            <div class="trend-bars">
              <i class="created" :style="{ height: barHeight(point.created_count) }" />
              <i class="decided" :style="{ height: barHeight(point.decided_count) }" />
              <b v-if="point.timed_out_count" :aria-label="`${point.timed_out_count} 项超时`">{{ point.timed_out_count }}</b>
            </div>
            <small v-if="index === 0 || index === state.trend.length - 1 || (state.trend.length <= 14 && index % 3 === 0)">{{ shortDate(point.date) }}</small>
            <small v-else aria-hidden="true">&nbsp;</small>
          </div>
          <div v-if="!state.loading && !state.trend.length" class="trend-empty">当前窗口暂无审批事实</div>
        </div>
        <div class="trend-legend" aria-hidden="true"><span><i class="created" />新建</span><span><i class="decided" />已决定</span><span><i class="timeout" />超时</span></div>
      </section>

      <div class="queue-summary">
        <strong>{{ state.status === 'PENDING' ? approvals.pendingCount.value : state.items.length }}</strong>
        <span>{{ state.status === 'PENDING' ? '项待处理' : '项记录' }}</span>
      </div>

      <div class="approval-list" aria-label="审批列表">
        <button
          v-for="item in state.items"
          :key="item.approval_id"
          type="button"
          :class="['approval-row', { selected: state.selected?.approval_id === item.approval_id }]"
          @click="safely(() => approvals.select(item))"
        >
          <span class="row-main">
            <strong>{{ item.title }}</strong>
            <small>{{ item.instructions || `节点 ${item.node_id}` }}</small>
          </span>
          <span :class="['status-pill', item.status.toLowerCase()]">{{ statusLabel(item.status) }}</span>
          <span class="row-meta">Run {{ item.run_id }} · 截止 {{ when(item.expires_at) }}</span>
          <span class="role-list"><span v-for="role in item.assignee_roles" :key="role">{{ role }}</span></span>
        </button>
        <div v-if="!state.loading && !state.items.length" class="empty-state">
          <strong>当前没有匹配的审批</strong>
          <span>待办到达后会显示审批角色、截止时间和关联 Run。</span>
        </div>
      </div>
    </section>

    <section class="domain-section" :id="sectionDomId('alerts')" data-menu-section="alerts">
      <div class="approval-heading">
        <div>
          <div class="eyebrow">Durable alert delivery</div>
          <h2 class="domain-section-title">审批告警与 Webhook 投递</h2>
          <p class="section-copy">到期风险先持久化为告警事实，再通过独立 Outbox 投递；第三方网络故障不会阻塞审批事务。</p>
        </div>
        <div class="heading-actions">
          <label class="status-filter">告警状态
            <select v-model="state.alertStatus" :disabled="state.loading" @change="safely(approvals.load)">
              <option value="OPEN">待响应</option>
              <option value="ACKNOWLEDGED">已确认</option>
              <option value="RESOLVED">已解决</option>
              <option value="">全部</option>
            </select>
          </label>
          <button class="btn ghost" :disabled="Boolean(state.action)" @click="safely(approvals.evaluateAlerts)">
            {{ state.action === 'evaluate-alerts' ? '评估中…' : '立即评估' }}
          </button>
        </div>
      </div>

      <div class="alert-metrics" aria-label="审批告警摘要">
        <article :class="['alert-metric', String(state.alertStats?.health || '').toLowerCase()]">
          <span>告警健康</span><strong>{{ healthLabel(state.alertStats?.health) }}</strong>
          <small>开放告警与投递死信综合判断</small>
        </article>
        <article class="alert-metric"><span>开放 / 严重</span><strong>{{ state.alertStats?.open_count || 0 }} / {{ state.alertStats?.critical_count || 0 }}</strong><small>已确认 {{ state.alertStats?.acknowledged_count || 0 }} 项</small></article>
        <article :class="['alert-metric', { critical: (state.alertStats?.delivery_dead_letter_count || 0) > 0 }]"><span>投递死信</span><strong>{{ state.alertStats?.delivery_dead_letter_count || 0 }}</strong><small>需配置目标后人工重放</small></article>
        <article class="alert-metric"><span>Webhook 目标</span><strong class="target-host">{{ state.alertConfig?.destination_configured ? state.alertConfig.destination_host : '未配置' }}</strong><small>{{ state.alertConfig?.destination_configured ? `最多重试 ${state.alertConfig.max_attempts} 次` : '通过部署环境变量配置，不在数据库保存 URL' }}</small></article>
      </div>

      <div class="alert-list" aria-label="审批告警列表">
        <article v-for="alert in state.alerts" :key="alert.alert_id" :class="['alert-row', alert.severity.toLowerCase()]">
          <div class="alert-row-title">
            <span :class="['severity-dot', alert.severity.toLowerCase()]" aria-hidden="true" />
            <div><strong>{{ alertTypeLabel(alert.alert_type) }}</strong><small>{{ alert.message }}</small></div>
          </div>
          <span :class="['status-pill', alert.status.toLowerCase()]">{{ alertStatusLabel(alert.status) }}</span>
          <dl>
            <dt>Approval</dt><dd class="mono">{{ alert.approval_id }}</dd>
            <dt>打开时间</dt><dd>{{ when(alert.opened_at) }}</dd>
            <dt>投递</dt><dd>{{ deliveryStatusLabel(alert.delivery?.status) }}<span v-if="alert.delivery?.attempt_count"> · {{ alert.delivery.attempt_count }}/{{ alert.delivery.max_attempts }}</span></dd>
            <dt v-if="alert.delivery?.last_error">最近错误</dt><dd v-if="alert.delivery?.last_error" class="delivery-error">{{ alert.delivery.last_error }}</dd>
          </dl>
          <div class="alert-actions">
            <button v-if="alert.status === 'OPEN'" class="btn ghost" :disabled="Boolean(state.action)" @click="safely(() => approvals.acknowledgeAlert(alert))">
              {{ state.action === `ack:${alert.alert_id}` ? '确认中…' : '确认告警' }}
            </button>
            <button v-if="['DISABLED', 'DEAD_LETTER'].includes(alert.delivery?.status)" class="btn ghost" :disabled="Boolean(state.action) || !state.alertConfig?.destination_configured" @click="safely(() => approvals.replayAlert(alert))">
              {{ state.action === `replay:${alert.alert_id}` ? '重放中…' : '重放投递' }}
            </button>
          </div>
        </article>
        <div v-if="!state.loading && !state.alerts.length" class="empty-state">
          <strong>当前筛选条件下没有审批告警</strong>
          <span>扫描器会对临期、逾期和自动超时分别生成一次幂等告警。</span>
        </div>
      </div>
    </section>

    <section class="domain-section" :id="sectionDomId('detail')" data-menu-section="detail">
      <div class="approval-heading">
        <div><h2 class="domain-section-title">审批详情</h2><p class="section-copy">决定采用乐观锁与服务端幂等，同一审批不能被相反决定覆盖。</p></div>
      </div>

      <div v-if="state.detailLoading" class="detail-placeholder" role="status">正在加载审批上下文…</div>
      <div v-else-if="state.selected" class="detail-grid">
        <article class="approval-card">
          <div class="card-title">
            <div><div class="eyebrow">{{ state.selected.node_type }}</div><strong>{{ state.selected.title }}</strong></div>
            <span :class="['status-pill', state.selected.status.toLowerCase()]">{{ statusLabel(state.selected.status) }}</span>
          </div>
          <p class="instructions">{{ state.selected.instructions || '未填写审批说明。' }}</p>
          <dl>
            <dt>审批 ID</dt><dd class="mono">{{ state.selected.approval_id }}</dd>
            <dt>Run ID</dt><dd class="mono">{{ state.selected.run_id }}</dd>
            <dt>节点</dt><dd class="mono">{{ state.selected.node_id }}</dd>
            <dt>指派角色</dt><dd>{{ state.selected.assignee_roles.join('、') }}</dd>
            <dt>创建时间</dt><dd>{{ when(state.selected.created_at) }}</dd>
            <dt>截止时间</dt><dd>{{ when(state.selected.expires_at) }}</dd>
            <dt v-if="state.selected.status === 'DECIDED'">处理结果</dt><dd v-if="state.selected.status === 'DECIDED'">{{ decisionLabel(state.selected) }}</dd>
            <dt v-if="state.selected.decided_by">处理人</dt><dd v-if="state.selected.decided_by" class="mono">{{ state.selected.decided_by }}</dd>
            <dt v-if="state.selected.decision_reason">处理理由</dt><dd v-if="state.selected.decision_reason">{{ state.selected.decision_reason }}</dd>
          </dl>

          <div v-if="state.selected.status === 'PENDING' && !state.decisionDraft" class="decision-actions">
            <button class="btn primary" @click="approvals.beginDecision('approve')">同意并恢复</button>
            <button class="btn danger" @click="approvals.beginDecision('reject')">拒绝并恢复</button>
          </div>

          <form v-if="state.decisionDraft" class="decision-panel" @submit.prevent="safely(approvals.submitDecision)">
            <div>
              <div class="eyebrow">{{ decisionTitle }}</div>
              <strong>{{ state.decisionDraft === 'approve' ? '工作流将沿 approve 分支恢复' : '工作流将沿 reject 分支恢复' }}</strong>
            </div>
            <label>处理理由{{ state.decisionDraft === 'reject' ? '（拒绝时必填）' : '（选填）' }}
              <textarea v-model="state.reason" maxlength="1000" rows="4" placeholder="说明判断依据，便于后续审计" />
            </label>
            <div class="decision-actions">
              <button type="button" class="btn ghost" :disabled="Boolean(state.action)" @click="approvals.cancelDecision">返回</button>
              <button type="submit" :class="['btn', state.decisionDraft === 'reject' ? 'danger solid' : 'primary']" :disabled="!approvals.canSubmitDecision.value">
                {{ state.action ? '提交中…' : decisionTitle }}
              </button>
            </div>
          </form>
        </article>

        <article class="run-card">
          <div class="card-title"><div><div class="eyebrow">关联运行</div><strong>{{ state.run?.stage || '—' }}</strong></div><span :class="['status-pill', String(state.run?.status || '').toLowerCase()]">{{ state.run?.status || '未知' }}</span></div>
          <div class="progress-track" :aria-label="`运行进度 ${state.run?.progress || 0}%`"><span :style="{ width: `${state.run?.progress || 0}%` }" /></div>
          <dl>
            <dt>Workflow</dt><dd class="mono">{{ state.run?.workflow_id || '—' }}</dd>
            <dt>版本</dt><dd>v{{ state.run?.workflow_version_no || 0 }}</dd>
            <dt>创建人</dt><dd class="mono">{{ state.run?.created_by || '—' }}</dd>
            <dt>当前进度</dt><dd>{{ state.run?.progress || 0 }}%</dd>
          </dl>
        </article>
      </div>
      <div v-else class="empty-state standalone"><strong>选择一项审批查看详情</strong><span>此处会显示审批政策、处理入口和关联运行状态。</span></div>
    </section>

    <section class="domain-section" :id="sectionDomId('audit')" data-menu-section="audit">
      <div class="approval-heading"><div><h2 class="domain-section-title">节点与事件审计</h2><p class="section-copy">检查审批前节点、等待状态、决定事件以及恢复后的执行轨迹。</p></div></div>
      <div class="audit-grid">
        <div class="table-wrap">
          <table>
            <thead><tr><th>节点</th><th>类型</th><th>状态</th><th>耗时</th></tr></thead>
            <tbody>
              <tr v-for="node in state.nodes" :key="node.node_execution_id"><td class="mono">{{ node.node_id }}</td><td>{{ node.node_type }}</td><td>{{ node.status }}</td><td>{{ node.duration_ms == null ? '—' : `${node.duration_ms} ms` }}</td></tr>
              <tr v-if="!state.nodes.length"><td colspan="4" class="empty-cell" role="status" aria-live="polite" aria-atomic="true">尚未选择审批或暂无节点记录</td></tr>
            </tbody>
          </table>
        </div>
        <ol class="event-list">
          <li v-for="event in state.events" :key="event.event_id"><span class="event-seq">#{{ event.sequence_no }}</span><div><strong>{{ event.event_type }}</strong><small>{{ event.stage }} · {{ when(event.created_at) }}</small></div></li>
          <li v-if="!state.events.length" class="empty-event">暂无事件记录</li>
        </ol>
      </div>
    </section>
  </section>
</template>

<style scoped>
.approval-page{display:grid;gap:12px}.approval-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}.section-copy{margin:4px 0 0;color:#687a92;max-width:760px;line-height:1.55}.eyebrow{text-transform:uppercase;letter-spacing:.08em;color:#74869d;font-size:10px;font-weight:800;margin-bottom:6px}.heading-actions,.decision-actions{display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap}.status-filter{display:grid;gap:5px;color:#53667e;font-size:12px;font-weight:700}.status-filter select{min-width:132px}.sla-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:16px 0}.sla-card{display:grid;gap:5px;min-width:0;padding:13px;border:1px solid #dce6f2;border-radius:10px;background:#fbfdff}.sla-card>span,.sla-card small{color:#687a92;font-size:11px}.sla-card strong{font-size:20px;color:#17335c}.sla-card.healthy{border-color:#b9ddc7;background:#f4fbf6}.sla-card.warn{border-color:#ead5a0;background:#fffaf0}.sla-card.critical{border-color:#efc3bf;background:#fff7f6}.sla-card.critical strong{color:#a92d24}.trend-panel{padding:16px;border:1px solid #dce6f2;border-radius:12px;background:#fbfdff}.trend-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.trend-heading h3{margin:0;color:#17335c;font-size:16px}.trend-heading p{margin:5px 0 0;color:#687a92;font-size:12px;line-height:1.5}.trend-summary{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0 8px;color:#687a92;font-size:11px}.trend-summary span{display:flex;align-items:baseline;gap:5px}.trend-summary strong{color:#17335c;font-size:18px}.trend-summary .risk strong{color:#a92d24}.trend-chart{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(20px,1fr);gap:4px;min-height:144px;padding:10px 8px 0;border:1px solid #e5ecf4;border-radius:9px;background:linear-gradient(to bottom,#f4f7fb 1px,transparent 1px);background-size:100% 33.333%;overflow-x:auto}.trend-day{display:grid;grid-template-rows:110px 18px;gap:4px;min-width:20px;text-align:center}.trend-bars{position:relative;display:flex;align-items:flex-end;justify-content:center;gap:2px;height:110px}.trend-bars i{display:block;width:min(7px,42%);min-height:0;border-radius:3px 3px 1px 1px}.trend-bars .created,.trend-legend .created{background:#91add7}.trend-bars .decided,.trend-legend .decided{background:#356fca}.trend-bars b{position:absolute;top:0;right:0;min-width:14px;padding:1px 3px;border-radius:999px;background:#b42318;color:#fff;font-size:9px;line-height:13px}.trend-day small{color:#8291a5;font-size:9px;white-space:nowrap}.trend-empty{display:grid;place-items:center;min-width:280px;color:#74869d;font-size:12px}.trend-legend{display:flex;justify-content:flex-end;gap:12px;margin-top:7px;color:#687a92;font-size:10px}.trend-legend span{display:flex;align-items:center;gap:4px}.trend-legend i{width:8px;height:8px;border-radius:2px}.trend-legend .timeout{border-radius:50%;background:#b42318}.queue-summary{display:flex;align-items:baseline;gap:6px;margin:14px 0 8px;color:#687a92}.queue-summary strong{font-size:28px;color:#17335c}.approval-list{border:1px solid #dce6f2;border-radius:12px;overflow:hidden;background:#fff}.approval-row{width:100%;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px 14px;padding:14px;text-align:left;border:0;border-bottom:1px solid #edf2f8;background:#fff;color:inherit;cursor:pointer;transition:transform 140ms cubic-bezier(.23,1,.32,1),background-color 140ms ease}.approval-row:last-child{border-bottom:0}.approval-row:active,.btn:active:not(:disabled){transform:scale(.97)}.approval-row.selected{background:#edf4ff}.row-main{display:grid;gap:3px;min-width:0}.row-main small{color:#687a92;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.row-meta{font-size:11px;color:#74869d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.role-list{display:flex;gap:4px;justify-content:flex-end;flex-wrap:wrap}.role-list span{padding:2px 6px;border-radius:5px;background:#eef3f8;color:#53667e;font:10px ui-monospace,SFMono-Regular,Consolas,monospace}.status-pill{align-self:start;display:inline-flex;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:750;background:#eef1f5;color:#566477}.status-pill.pending,.status-pill.waiting_approval{background:#fff4d8;color:#865a00}.status-pill.decided,.status-pill.succeeded{background:#e5f6eb;color:#18723a}.status-pill.cancelled,.status-pill.failed{background:#fee9e7;color:#a92d24}.detail-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:14px;margin-top:14px}.approval-card,.run-card{border:1px solid #dce6f2;border-radius:12px;padding:16px;background:#fff}.card-title{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.card-title strong{font-size:18px}.instructions{color:#526a84;line-height:1.6;padding:12px;background:#f5f8fc;border-radius:9px}.approval-card dl,.run-card dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 14px;font-size:12px}.approval-card dt,.run-card dt{color:#74869d}.approval-card dd,.run-card dd{margin:0;word-break:break-word}.decision-actions{margin-top:16px}.decision-panel{display:grid;gap:14px;margin-top:16px;padding:14px;border:1px solid #d4dfed;border-radius:10px;background:#f8fbff}.decision-panel label{display:grid;gap:7px;color:#53667e;font-size:12px;font-weight:700}.btn{transition:transform 140ms cubic-bezier(.23,1,.32,1),background-color 140ms ease,border-color 140ms ease}.btn.danger{color:#b42318;border-color:#efc3bf;background:#fff8f7}.btn.danger.solid{color:#fff;border-color:#b42318;background:#b42318}.progress-track{height:7px;margin:16px 0;border-radius:999px;background:#e8eef6;overflow:hidden}.progress-track span{display:block;height:100%;border-radius:inherit;background:#477bd8}.audit-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(300px,.8fr);gap:14px;margin-top:14px}.event-list{list-style:none;margin:0;padding:0;border:1px solid #dce6f2;border-radius:12px;overflow:auto;max-height:360px}.event-list li{display:grid;grid-template-columns:auto 1fr;gap:10px;padding:10px 12px;border-bottom:1px solid #edf2f8}.event-list li:last-child{border-bottom:0}.event-list div{display:grid;gap:3px}.event-list small{color:#74869d}.event-seq{font:11px ui-monospace,SFMono-Regular,Consolas,monospace;color:#74869d}.empty-state{display:grid;gap:5px;padding:28px 18px;text-align:center;color:#687a92}.empty-state strong{color:#405975}.empty-state.standalone,.detail-placeholder{margin-top:14px;border:1px dashed #dce6f2;border-radius:12px}.detail-placeholder{padding:28px;text-align:center;color:#687a92}.empty-cell{text-align:center;padding:28px;color:#74869d}.empty-event{text-align:center!important;color:#74869d}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}@media(hover:hover) and (pointer:fine){.approval-row:hover{background:#f6f9fd}}@media(max-width:1180px){.sla-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:960px){.detail-grid,.audit-grid{grid-template-columns:1fr}.approval-heading{align-items:stretch;flex-direction:column}}@media(max-width:600px){.sla-grid{grid-template-columns:1fr 1fr}.approval-row{grid-template-columns:minmax(0,1fr)}.role-list{justify-content:flex-start}.heading-actions,.trend-heading{align-items:stretch;flex-direction:column}.status-filter,.status-filter select{width:100%}}@media(max-width:420px){.sla-grid{grid-template-columns:1fr}}@media(prefers-reduced-motion:reduce){.approval-row,.btn{transition:background-color 100ms ease,border-color 100ms ease}.approval-row:active,.btn:active:not(:disabled){transform:none}}
</style>

<style scoped>
.approval-page{min-width:0;grid-template-columns:minmax(0,1fr)}
.approval-page>.domain-section{min-width:0}
.trend-panel{box-sizing:border-box;min-width:0;max-width:100%}
.trend-chart{box-sizing:border-box;width:100%;min-width:0;max-width:100%}
.alert-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:16px 0}
.alert-metric{display:grid;gap:5px;min-width:0;padding:13px;border:1px solid #dce6f2;border-radius:10px;background:#fbfdff}
.alert-metric>span,.alert-metric small{color:#687a92;font-size:11px}
.alert-metric strong{color:#17335c;font-size:20px}
.alert-metric.healthy{border-color:#b9ddc7;background:#f4fbf6}
.alert-metric.warn{border-color:#ead5a0;background:#fffaf0}
.alert-metric.critical{border-color:#efc3bf;background:#fff7f6}
.alert-metric.critical strong{color:#a92d24}
.alert-metric .target-host{overflow:hidden;text-overflow:ellipsis;font-size:14px;white-space:nowrap}
.alert-list{display:grid;gap:8px}
.alert-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px 16px;min-width:0;padding:14px;border:1px solid #dce6f2;border-left:3px solid #d8a63c;border-radius:10px;background:#fff}
.alert-row.critical{border-left-color:#c4483d}
.alert-row-title{display:flex;align-items:flex-start;gap:9px;min-width:0}
.alert-row-title>div{display:grid;gap:3px;min-width:0}
.alert-row-title small{color:#687a92}
.severity-dot{flex:0 0 auto;width:8px;height:8px;margin-top:5px;border-radius:50%;background:#d8a63c}
.severity-dot.critical{background:#c4483d}
.alert-row dl{grid-column:1/-1;display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 12px;margin:0;font-size:11px}
.alert-row dt{color:#74869d}
.alert-row dd{min-width:0;margin:0;overflow-wrap:anywhere;color:#405975}
.delivery-error{color:#a92d24!important}
.alert-actions{grid-column:1/-1;display:flex;gap:8px;justify-content:flex-end}
.status-pill.open{background:#fee9e7;color:#a92d24}
.status-pill.acknowledged{background:#fff4d8;color:#865a00}
.status-pill.resolved{background:#e5f6eb;color:#18723a}
@media(max-width:960px){.alert-metrics{grid-template-columns:1fr 1fr}}
@media(max-width:600px){.alert-metrics{grid-template-columns:1fr}.alert-row{grid-template-columns:minmax(0,1fr)}.alert-row>.status-pill{grid-row:2}.alert-actions{justify-content:stretch}.alert-actions .btn{flex:1}}
</style>
