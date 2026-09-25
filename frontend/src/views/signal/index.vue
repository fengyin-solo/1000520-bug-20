<template>
  <section class="page" data-module="signal">
    <header class="page-head">
      <div>
        <h2>信号机管理</h2>
        <p class="page-desc">下次检修日按「上次检修日 + 检修周期」统一重算；超期设备排在最前，缺上次检修日的设备单独列出。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记信号机</button>
        <button class="btn" type="button" @click="exportRows">导出信号机清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">正常队列设备</span>
        <strong class="stat-value">{{ total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已超期（排在最前）</span>
        <strong class="stat-value overdue-text">{{ overdue }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待排期（缺上次检修日）</span>
        <strong class="stat-value">{{ unscheduled.length }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="applyFilters">
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model.trim="keywordDraft" placeholder="按设备编号检索，忽略空格大小写" />
      </label>
      <label class="filter-item">
        <span>设备状态</span>
        <select v-model="statusDraft">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">
            <button
              v-if="column === '到期日'"
              class="link sort-link"
              type="button"
              :title="`到期日排序：${sort === 'due_asc' ? '当前升序' : '当前降序'}（超期始终最前）`"
              @click="toggleSort"
            >
              到期日 {{ sort === 'due_asc' ? '▲' : '▼' }}
            </button>
            <template v-else>{{ column === '下次检修日' ? '登记的下次检修日' : column }}</template>
          </th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-overdue': row['超期'] }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '到期状态'">
              <span :class="['due-badge', dueBadgeClass(row[column])]">{{ row[column] }}</span>
            </template>
            <template v-else-if="column === '到期核对'">
              <span :class="{ 'mismatch-text': row[column] && row[column] !== '一致' }">{{ row[column] || '—' }}</span>
            </template>
            <template v-else>{{ displayValue(row, column) }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">当前条件下没有到期口径完整的信号机</td>
        </tr>
      </tbody>
    </table>

    <section v-if="unscheduled.length" class="unscheduled-block">
      <h3>待排期设备（{{ unscheduled.length }}）</h3>
      <p class="page-desc">缺少上次检修日或检修周期无法识别，不参与到期排序，补录后自动回到正常队列。</p>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in unscheduledColumns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in unscheduled" :key="String(row.id)">
            <td v-for="column in unscheduledColumns" :key="column">
              <span v-if="column === '待排期原因'" class="mismatch-text">{{ row[column] }}</span>
              <template v-else>{{ displayValue(row, column) }}</template>
            </td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条信号机记录，第 {{ page }} / {{ totalPages }} 页</span>
      <span class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="goPage(1)">首页</button>
        <button class="btn" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="goPage(totalPages)">末页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/signal'
const columns = ['设备编号', '设备类型', '安装位置', '显示制式', '所属区段', '上次检修日', '检修周期', '下次检修日', '到期日', '到期状态', '到期核对', '设备状态']
const unscheduledColumns = ['设备编号', '设备类型', '安装位置', '所属区段', '上次检修日', '检修周期', '待排期原因', '设备状态']
const actions = ['确认检修', '登记故障', '更换设备']
const statuses = ['待检修', '运用正常', '故障停用', '已更换']
const PAGE_SIZE = 10

const route = useRoute()
const router = useRouter()

const rows = ref<Row[]>([])
const unscheduled = ref<Row[]>([])
const total = ref(0)
const overdue = ref(0)
const errorMessage = ref('')

// 输入框草稿与 URL 解耦：点查询才写入地址，翻页/排序直接写地址。
const keywordDraft = ref(String(route.query.keyword ?? ''))
const statusDraft = ref(String(route.query.status ?? ''))

const keyword = computed(() => String(route.query.keyword ?? ''))
const status = computed(() => String(route.query.status ?? ''))
const sort = computed(() => (route.query.sort === 'due_desc' ? 'due_desc' : 'due_asc'))
const page = computed(() => {
  const value = Number.parseInt(String(route.query.page ?? '1'), 10)
  return Number.isFinite(value) && value > 0 ? value : 1
})
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

watch([keyword, status], ([nextKeyword, nextStatus]) => {
  keywordDraft.value = nextKeyword
  statusDraft.value = nextStatus
})

function pushQuery(next: Record<string, string>) {
  // 空值不带进地址；条件、排序、页码全部落在 URL 上，返回本页时原样恢复。
  const query: Record<string, string> = {}
  for (const [key, value] of Object.entries(next)) {
    if (value) query[key] = value
  }
  const current = JSON.stringify(route.query)
  const target = JSON.stringify(query)
  // 条件没变化时不再推一次相同地址，避免 vue-router 的重复导航报错。
  if (current === target) return
  void router.push({ name: 'signal', query })
}

function applyFilters() {
  pushQuery({ keyword: keywordDraft.value, status: statusDraft.value, sort: sort.value, page: '1' })
}

function resetFilters() {
  keywordDraft.value = ''
  statusDraft.value = ''
  pushQuery({})
}

function toggleSort() {
  pushQuery({
    keyword: keyword.value,
    status: status.value,
    sort: sort.value === 'due_asc' ? 'due_desc' : 'due_asc',
    page: String(page.value),
  })
}

function goPage(target: number) {
  const clamped = Math.min(Math.max(target, 1), totalPages.value)
  pushQuery({ keyword: keyword.value, status: status.value, sort: sort.value, page: String(clamped) })
}

function exportRows() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (status.value) query.set('status', status.value)
  if (sort.value !== 'due_asc') query.set('sort', sort.value)
  const suffix = query.toString()
  window.open(`${ENDPOINT}/export${suffix ? `?${suffix}` : ''}`, '_blank')
}

function openCreate() {
  errorMessage.value = '信号机登记入口尚未接入审批流'
}

function displayValue(row: Row, column: string): string {
  const value = row[column]
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function dueBadgeClass(state: string | number | boolean | null): string {
  if (state === '超期') return 'badge-overdue'
  if (state === '今日到期') return 'badge-today'
  return 'badge-normal'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = (await response.json().catch(() => null)) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '信号机动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '信号机操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (keyword.value) params.set('keyword', keyword.value)
  if (status.value) params.set('status', status.value)
  params.set('sort', sort.value)
  params.set('page', String(page.value))
  params.set('size', String(PAGE_SIZE))
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('信号机列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    unscheduled.value = payload.unscheduled_items ?? []
    total.value = payload.total ?? rows.value.length
    overdue.value = payload.overdue ?? 0
    // 筛选后结果变少、当前页超界时回到最后一页，避免停在空白页。
    if (page.value > totalPages.value) {
      goPage(totalPages.value)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '信号机列表读取失败'
  }
}

// 同一组件内 query 变化（翻页、查询、返回）不会重新挂载，靠 watch 触发重载。
watch([keyword, status, sort, page], () => {
  void reload()
}, { immediate: true })
</script>

<style scoped>
.sort-link { font: inherit; font-weight: 600; }
.row-overdue { background: #fef3f2; }
.overdue-text { color: #b42318; }
.mismatch-text { color: #b54708; }
.due-badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.badge-overdue { background: #fee4e2; color: #b42318; }
.badge-today { background: #fef0c7; color: #b54708; }
.badge-normal { background: #e7f0ff; color: #1f6feb; }
.unscheduled-block { margin-top: 18px; }
.unscheduled-block h3 { font-size: 14px; margin: 0 0 4px; }
.pager { display: flex; gap: 6px; }
.pager .btn:disabled { opacity: 0.5; cursor: not-allowed; }
select { padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; background: #fff; }
</style>
