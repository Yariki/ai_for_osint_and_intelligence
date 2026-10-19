<script setup lang="ts">
defineProps<{ page: number; total: number; pageSize: number }>()
defineEmits<{ change: [page: number] }>()
</script>
<template>
  <div class="pagination">
    <span>{{ total.toLocaleString() }} results · {{ pageSize }} per page</span>
    <v-pagination
      :model-value="page"
      :length="Math.max(1, Math.ceil(total / pageSize))"
      :total-visible="5"
      density="comfortable"
      size="small"
      aria-label="Results pages"
      @update:model-value="$emit('change', $event)"
    />
  </div>
</template>
