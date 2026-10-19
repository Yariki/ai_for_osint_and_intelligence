<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { mdiMagnify, mdiRefresh } from '@mdi/js'
import { api, dateTime, messageOf } from '../api'
import type { Article, Page, Source, Tag } from '../types'
import PageControls from '../components/PageControls.vue'
const articles = ref<Article[]>([])
const sources = ref<Source[]>([])
const tags = ref<Tag[]>([])
const selectedTags = ref<number[]>([])
const sourceId = ref<number | null>(null)
const search = ref('')
const from = ref('')
const to = ref('')
const page = ref(1)
const total = ref(0)
const pageSize = 20
const busy = ref(false)
const error = ref('')
let requestNumber = 0
let alive = true
async function load(newPage = page.value) {
  const current = ++requestNumber
  page.value = newPage
  busy.value = true
  const query = new URLSearchParams({ page: String(newPage), page_size: String(pageSize) })
  if (search.value?.trim()) query.set('search', search.value.trim())
  if (sourceId.value) query.set('source_id', String(sourceId.value))
  selectedTags.value.forEach((id) => query.append('tag_ids', String(id)))
  if (from.value) query.set('published_from', `${from.value}T00:00:00Z`)
  if (to.value) query.set('published_to', `${to.value}T23:59:59.999999Z`)
  try {
    const result = await api<Page<Article>>(`/articles?${query}`)
    if (alive && current === requestNumber) {
      articles.value = result.items
      total.value = result.total
      error.value = ''
    }
  } catch (err) {
    if (alive && current === requestNumber) error.value = messageOf(err)
  } finally {
    if (current === requestNumber) busy.value = false
  }
}
function clear() {
  search.value = ''
  selectedTags.value = []
  sourceId.value = null
  from.value = ''
  to.value = ''
  void load(1)
}
function selectTag(id: number) {
  selectedTags.value = [id]
  void load(1)
}
onMounted(async () => {
  try {
    ;[sources.value, tags.value] = await Promise.all([
      api<Source[]>('/sources'),
      api<Tag[]>('/tags'),
    ])
    await load()
  } catch (err) {
    error.value = messageOf(err)
  }
})
onUnmounted(() => {
  alive = false
})
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>The reading room.</h1>
      <p class="subtitle">
        Every story in one place. Find your next read by source, category, or date.
      </p>
    </div>
    <v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="busy" @click="load()">
      Refresh
    </v-btn>
  </div>
  <v-alert v-if="error" type="error">{{ error }}</v-alert>
  <v-card class="table-card">
    <form class="filters" @submit.prevent="load(1)">
      <v-row dense>
        <v-col cols="12" md="5">
          <v-text-field
            v-model="search"
            label="Search article titles"
            :prepend-inner-icon="mdiMagnify"
            clearable
            @click:clear="search = ''"
          />
        </v-col>
        <v-col cols="12" sm="6" md="4">
          <v-select
            v-model="selectedTags"
            :items="tags"
            item-title="name"
            item-value="id"
            label="Filter by tags (any match)"
            multiple
            chips
            closable-chips
            clearable
            @update:model-value="load(1)"
          />
        </v-col>
        <v-col cols="12" sm="6" md="3">
          <v-select
            v-model="sourceId"
            :items="sources"
            item-title="name"
            item-value="id"
            label="All sources"
            clearable
            @update:model-value="load(1)"
          />
        </v-col>
        <v-col cols="12" sm="4" md="3">
          <v-text-field v-model="from" type="date" label="Published from (UTC)" />
        </v-col>
        <v-col cols="12" sm="4" md="3">
          <v-text-field v-model="to" type="date" label="Published through (UTC)" />
        </v-col>
        <v-col cols="12" sm="4" md="6" class="d-flex align-start ga-2 pt-2">
          <v-btn type="submit" color="primary">Apply filters</v-btn>
          <v-btn variant="text" @click="clear">Clear</v-btn>
        </v-col>
      </v-row>
    </form>
    <v-progress-linear v-if="busy" indeterminate color="primary" />
    <v-table>
      <thead>
        <tr>
          <th>Article</th>
          <th>Categories</th>
          <th>Published</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="article in articles" :key="article.id">
          <td style="min-width: 260px">
            <a class="article-title" :href="article.link" target="_blank" rel="noopener noreferrer">
              {{ article.title }}
            </a>
            <p class="article-description">{{ article.description }}</p>
            <div class="stat-note mt-2">
              {{
                article.source_ids
                  .map((id) => sources.find((s) => s.id === id)?.name ?? `Source ${id}`)
                  .join(' · ')
              }}
            </div>
          </td>
          <td style="min-width: 160px; max-width: 300px">
            <v-chip
              v-for="tag in article.tags"
              :key="tag.id"
              size="x-small"
              color="secondary"
              class="ma-1"
              @click="selectTag(tag.id)"
            >
              {{ tag.name }}
            </v-chip>
            <span v-if="!article.tags.length" class="stat-note">Uncategorized</span>
          </td>
          <td class="text-no-wrap">{{ dateTime(article.published_at) }}</td>
        </tr>
      </tbody>
    </v-table>
    <div v-if="!busy && !articles.length" class="empty-state">
      No articles match these filters. Try another category, or run a source to collect your first
      stories.
    </div>
    <PageControls :page="page" :total="total" :page-size="pageSize" @change="load" />
  </v-card>
</template>
