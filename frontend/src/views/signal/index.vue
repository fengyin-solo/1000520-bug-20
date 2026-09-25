<template>
  <section class="page" data-module="signal">
    <header class="page-head">
      <div>
        <h2>信号机管理</h2>
        <p class="page-desc">维护信号机，围绕设备编号、设备类型、安装位置、显示制式做登记、筛选与状态流转。下次检修日按上次检修日+检修周期重算，超期设备排在前面。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记信号机</button>
        <button class="btn" type="button" @click="exportRows">导出信号机清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="applyFilters">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">
            <button
              v-if="column === '下次检修日'"
              class="sort-head"
              type="button"
              @click="toggleSort"
            >
              {{ column }}{{ sort === 'due' ? ' ↑' : ' ↓' }}
            </button>
            <template v-else>{{ column }}</template>
          </th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-overdue': row.overdue }">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '下次检修日'">
              {{ row[column] || '—' }}<span v-if="row.overdue" class="overdue-tag">超期</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无信号机数据，可先登记信号机</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条信号机记录</span>
      <span class="pager">
        <button class="btn ghost" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <button class="btn ghost" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="missingTotal > 0" class="missing-block">
      <h3>缺上次检修日的设备（{{ missingTotal }} 台，不进入正常检修队列）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in missingRows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
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
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/signal'
const columns = ["设备编号", "设备类型", "安装位置", "显示制式", "所属区段", "上次检修日", "检修周期", "下次检修日", "设备状态"]
const actions = ["确认检修", "登记故障", "更换设备"]
const stats = [{"label": "在运信号机", "value": 0}, {"label": "待检修信号机", "value": 0}, {"label": "故障停用台数", "value": 0}]
const filterFields = columns.slice(0, 3)
const PAGE_SIZE = 20

const route = useRoute()
const router = useRouter()

const rows = ref<Row[]>([])
const missingRows = ref<Row[]>([])
const missingTotal = ref(0)
const total = ref(0)
const page = ref(1)
const sort = ref<'due' | 'due_desc'>('due')
const filters = ref<Record<string, string>>({})
const errorMessage = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

// 条件、排序、页码都落在地址栏上：翻页、刷新、从别的页面返回都能原样恢复。
function readQuery() {
  const query = route.query
  const next: Record<string, string> = {}
  for (const field of filterFields) {
    const value = query[field]
    if (typeof value === 'string' && value) {
      next[field] = value
    }
  }
  filters.value = next
  sort.value = query.sort === 'due_desc' ? 'due_desc' : 'due'
  const rawPage = Number(query.page)
  page.value = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1
}

function buildQuery() {
  const query: Record<string, string> = {}
  for (const field of filterFields) {
    if (filters.value[field]) {
      query[field] = filters.value[field]
    }
  }
  if (sort.value !== 'due') {
    query.sort = sort.value
  }
  if (page.value > 1) {
    query.page = String(page.value)
  }
  return query
}

function stateSnapshot() {
  return JSON.stringify([filters.value, sort.value, page.value])
}

let lastSnapshot = ''

async function syncFromRoute() {
  readQuery()
  const snapshot = stateSnapshot()
  if (snapshot === lastSnapshot) {
    return
  }
  lastSnapshot = snapshot
  await reload()
}

function applyState() {
  lastSnapshot = stateSnapshot()
  void router.replace({ query: buildQuery() })
  void reload()
}

function applyFilters() {
  page.value = 1
  applyState()
}

function resetFilters() {
  filters.value = {}
  sort.value = 'due'
  page.value = 1
  applyState()
}

function toggleSort() {
  sort.value = sort.value === 'due' ? 'due_desc' : 'due'
  page.value = 1
  applyState()
}

function goPage(target: number) {
  if (target < 1 || target > totalPages.value || target === page.value) {
    return
  }
  page.value = target
  applyState()
}

function currentParams() {
  const params = new URLSearchParams()
  for (const field of filterFields) {
    if (filters.value[field]) {
      params.set(field, filters.value[field])
    }
  }
  params.set('sort', sort.value)
  return params
}

function exportRows() {
  // 导出与列表页走同一套条件与排序，两个入口可以互相核对
  window.open(`${ENDPOINT}/export?${currentParams().toString()}`, '_blank')
}

function openCreate() {
  errorMessage.value = '信号机登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('信号机动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '信号机操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = currentParams()
  params.set('page', String(page.value))
  params.set('size', String(PAGE_SIZE))
  try {
    const [listResponse, missingResponse] = await Promise.all([
      request(`${ENDPOINT}?${params.toString()}`),
      request(`${ENDPOINT}/missing?${params.toString()}`),
    ])
    if (!listResponse.ok || !missingResponse.ok) {
      throw new Error('信号机列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const missingPayload = await missingResponse.json()
    missingRows.value = missingPayload.items ?? []
    missingTotal.value = missingPayload.total ?? missingRows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '信号机列表读取失败'
  }
}

onMounted(syncFromRoute)
watch(() => route.query, syncFromRoute)
</script>

<style scoped>
.sort-head {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  cursor: pointer;
  color: var(--brand);
}
.row-overdue td {
  background: #fef3f2;
}
.overdue-tag {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #b42318;
  color: #fff;
  font-size: 12px;
}
.pager {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pager .btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.missing-block {
  margin-top: 16px;
}
.missing-block h3 {
  font-size: 14px;
  color: var(--muted);
}
</style>
