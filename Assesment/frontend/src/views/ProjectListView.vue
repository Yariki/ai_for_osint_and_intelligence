<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError } from '../api/client'
import { type Project, useProjectsStore } from '../stores/projects'

const store = useProjectsStore()

const message = ref('')

// Create and edit share one dialog; `editing` is null while creating.
const formOpen = ref(false)
const editing = ref<Project | null>(null)
const name = ref('')
const description = ref('')
const formError = ref<ApiError | null>(null)

const deleting = ref<Project | null>(null)

onMounted(() => run(() => store.fetchAll()))

/** Run an action and show an API error as a message instead of failing silently. */
async function run(action: () => Promise<unknown>): Promise<void> {
  message.value = ''
  try {
    await action()
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    message.value = caught.message
  }
}

function openForm(project: Project | null): void {
  editing.value = project
  name.value = project?.name ?? ''
  description.value = project?.description ?? ''
  formError.value = null
  formOpen.value = true
}

async function submitForm(): Promise<void> {
  formError.value = null
  try {
    if (editing.value) await store.update(editing.value.id, name.value, description.value)
    else await store.create(name.value, description.value)
    formOpen.value = false
  } catch (caught) {
    if (!(caught instanceof ApiError)) throw caught
    formError.value = caught
  }
}

async function confirmDelete(): Promise<void> {
  const project = deleting.value
  deleting.value = null
  if (project) await run(() => store.remove(project.id))
}
</script>

<template>
  <v-container>
    <div class="d-flex align-center mb-4">
      <h1 class="text-h5">Projects</h1>
      <v-spacer />
      <v-btn color="primary" data-test="project-new" @click="openForm(null)">New project</v-btn>
    </div>

    <v-alert v-if="message" type="error" class="mb-4">{{ message }}</v-alert>

    <p v-if="store.projects.length === 0">No projects yet.</p>
    <v-table v-else>
      <thead>
        <tr>
          <th>Name</th>
          <th>Description</th>
          <th>State</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="project in store.projects" :key="project.id" data-test="project-row">
          <td>
            <router-link :to="{ name: 'project', params: { id: project.id } }">
              {{ project.name }}
            </router-link>
          </td>
          <td>{{ project.description }}</td>
          <td>
            <v-chip v-if="project.state === 'archived'" size="small">Archived, read-only</v-chip>
            <span v-else>Active</span>
          </td>
          <td class="text-right">
            <!-- An archived project is read-only: it can be opened and deleted, nothing else. -->
            <template v-if="project.state === 'active'">
              <v-btn variant="text" size="small" @click="openForm(project)">Edit</v-btn>
              <v-btn variant="text" size="small" @click="run(() => store.archive(project.id))">
                Archive
              </v-btn>
            </template>
            <v-btn variant="text" size="small" color="error" @click="deleting = project">
              Delete
            </v-btn>
          </td>
        </tr>
      </tbody>
    </v-table>

    <v-dialog v-model="formOpen" max-width="520">
      <v-card :title="editing ? 'Edit project' : 'New project'">
        <v-card-text>
          <v-alert v-if="formError && formError.details.length === 0" type="error" class="mb-4">
            {{ formError.message }}
          </v-alert>
          <v-text-field
            v-model="name"
            label="Name"
            :error-messages="formError?.fieldMessage('name')"
          />
          <v-textarea
            v-model="description"
            label="Description"
            rows="3"
            :error-messages="formError?.fieldMessage('description')"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="formOpen = false">Cancel</v-btn>
          <v-btn color="primary" :disabled="!name" @click="submitForm">Save</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="deleting !== null" max-width="520" @update:model-value="deleting = null">
      <v-card title="Delete project">
        <v-card-text>
          Delete <strong>{{ deleting?.name }}</strong> and all of its data? This cannot be undone.
          The audit log keeps its entries.
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn @click="deleting = null">Cancel</v-btn>
          <v-btn color="error" data-test="project-delete-confirm" @click="confirmDelete">
            Delete project
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>
