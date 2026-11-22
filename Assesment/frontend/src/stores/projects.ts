import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Schemas } from '../api/client'

export type Project = Schemas['ProjectOut']
export type Scope = Schemas['ScopeOut']

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<Project[]>([])

  function put(project: Project): Project {
    const index = projects.value.findIndex((known) => known.id === project.id)
    if (index === -1) projects.value.push(project)
    else projects.value[index] = project
    return project
  }

  async function fetchAll(): Promise<void> {
    // 200 is the server's page limit; an installation holds up to 100 projects (A7).
    const page = await api.get<Schemas['Page_ProjectOut_']>('/projects', { limit: 200 })
    projects.value = page.items
  }

  async function fetchOne(id: number): Promise<Project> {
    return put(await api.get<Project>(`/projects/${id}`))
  }

  async function create(name: string, description: string): Promise<Project> {
    return put(await api.post<Project>('/projects', { name, description }))
  }

  /** Rename or describe. */
  async function update(id: number, name: string, description: string): Promise<Project> {
    return put(await api.patch<Project>(`/projects/${id}`, { name, description }))
  }

  async function archive(id: number): Promise<Project> {
    return put(await api.post<Project>(`/projects/${id}/archive`))
  }

  /** Delete a project. Call this only after the analyst has confirmed the deletion. */
  async function remove(id: number): Promise<void> {
    await api.delete(`/projects/${id}`, { confirm: true })
    projects.value = projects.value.filter((project) => project.id !== id)
  }

  async function fetchScope(id: number): Promise<Scope> {
    return api.get<Scope>(`/projects/${id}/scope`)
  }

  /**
   * Replace the scope. `authorized` is the analyst's attestation; without it
   * nothing is sent. The entries go to the server exactly as they were typed:
   * the server alone decides what is valid.
   */
  async function saveScope(id: number, entries: string[], authorized: boolean): Promise<Scope> {
    if (!authorized) throw new Error('The scope cannot be saved without the attestation.')
    return api.put<Scope>(`/projects/${id}/scope`, { entries, authorized: true })
  }

  async function fetchScopeHistory(id: number): Promise<Scope[]> {
    const page = await api.get<Schemas['Page_ScopeOut_']>(`/projects/${id}/scope/history`, {
      limit: 200,
    })
    return page.items
  }

  return {
    projects,
    fetchAll,
    fetchOne,
    create,
    update,
    archive,
    remove,
    fetchScope,
    saveScope,
    fetchScopeHistory,
  }
})
