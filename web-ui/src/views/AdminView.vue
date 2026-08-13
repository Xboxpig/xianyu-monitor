<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import AccountsView from '@/views/AccountsView.vue'
import LogsView from '@/views/LogsView.vue'
import SettingsView from '@/views/SettingsView.vue'
import BlacklistPane from '@/components/admin/BlacklistPane.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

type AdminTab = 'accounts' | 'notifications' | 'blacklist' | 'logs' | 'system'

const tabs: { key: AdminTab; label: string }[] = [
  { key: 'accounts', label: t('admin.tabs.accounts') },
  { key: 'notifications', label: t('admin.tabs.notifications') },
  { key: 'blacklist', label: t('admin.tabs.blacklist') },
  { key: 'logs', label: t('admin.tabs.logs') },
  { key: 'system', label: t('admin.tabs.system') },
]

const validTabs = new Set(['accounts', 'notifications', 'blacklist', 'logs', 'system'])

// prompts 是系统设置里的内部页签，映射到 system 外层页签并透传 query
const activeTab = computed<AdminTab>(() => {
  const tab = typeof route.query.tab === 'string' ? route.query.tab : ''
  if (tab === 'prompts') return 'system'
  return validTabs.has(tab) ? (tab as AdminTab) : 'system'
})

function switchTab(tab: AdminTab) {
  router.replace({ query: { ...route.query, tab } })
}
</script>

<template>
  <div class="app-surface overflow-hidden">
    <div class="flex items-center justify-between border-b border-border px-5 pb-0 pt-4">
      <div class="flex gap-1 overflow-x-auto">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-bold transition-colors"
          :class="
            activeTab === tab.key
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          "
          @click="switchTab(tab.key)"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <div class="p-5">
      <div v-if="activeTab === 'accounts'" class="admin-embed">
        <AccountsView />
      </div>
      <div v-else-if="activeTab === 'notifications'" class="admin-embed">
        <SettingsView />
      </div>
      <div v-else-if="activeTab === 'blacklist'" class="admin-embed">
        <BlacklistPane />
      </div>
      <div v-else-if="activeTab === 'logs'" class="admin-embed">
        <LogsView />
      </div>
      <div v-else class="admin-embed">
        <SettingsView />
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-embed :deep(h1),
.admin-embed :deep(h1 + p) {
  display: none;
}

/* 日志页嵌入后避免过高，限制在标签页可视区内滚动 */
.admin-embed :deep(.h-\[calc\(100vh-100px\)\]) {
  height: calc(100vh - 13rem);
}
</style>
