<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  mdiViewDashboardOutline,
  mdiRss,
  mdiTextBoxMultipleOutline,
  mdiHistory,
  mdiMenu,
} from '@mdi/js'
const route = useRoute()
const drawer = ref(false)
const links = [
  { title: 'Overview', to: '/', icon: mdiViewDashboardOutline },
  { title: 'Articles', to: '/articles', icon: mdiTextBoxMultipleOutline },
  { title: 'Sources', to: '/sources', icon: mdiRss },
  { title: 'Crawl jobs', to: '/jobs', icon: mdiHistory },
]
</script>

<template>
  <v-app>
    <v-app-bar color="#191e26" flat height="72">
      <v-app-bar-nav-icon
        class="d-md-none"
        :icon="mdiMenu"
        aria-label="Open navigation"
        @click="drawer = !drawer"
      />
      <RouterLink class="brand" to="/">
        <span class="brand-mark">///</span>
        PITWALL
        <span class="brand-sub">RSS READER</span>
      </RouterLink>
      <v-spacer />
      <span class="header-note d-none d-sm-flex">
        <span class="status-dot" />
        YOUR FEED, IN FOCUS
      </span>
    </v-app-bar>
    <v-navigation-drawer
      :model-value="$vuetify.display.mdAndUp || drawer"
      :permanent="$vuetify.display.mdAndUp"
      width="220"
      color="#191e26"
      class="sidebar"
      @update:model-value="drawer = $event"
    >
      <div class="nav-label">WORKSPACE</div>
      <v-list nav class="px-3">
        <v-list-item
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          :exact="link.to === '/'"
          :prepend-icon="link.icon"
          :title="link.title"
          color="#ffac82"
          rounded="lg"
        />
      </v-list>
      <template #append>
        <div class="sidebar-foot">
          <span class="status-dot" />
          Single-user workspace
          <br />
          <span>Scheduled. Respectful. Incremental.</span>
        </div>
      </template>
    </v-navigation-drawer>
    <v-main>
      <main class="workspace">
        <div class="eyebrow">WORKSPACE / {{ route.meta.title }}</div>
        <RouterView />
        <footer>Feed content only · Times shown in your local timezone</footer>
      </main>
    </v-main>
  </v-app>
</template>
