import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Schemas } from '../api/client'

export type Profile = Schemas['ProfileOut']
export type ProfileIn = Schemas['ProfileIn']
export type Schedule = Schemas['ScheduleOut']
export type ScheduleIn = Schemas['ScheduleIn']
export type ScheduleRun = Schemas['ScheduleRunOut']

/** Scan profiles (FR-CFG-8) and the schedules of the project that is open (FR-SCH-2). */
export const useSchedulesStore = defineStore('schedules', () => {
  const profiles = ref<Profile[]>([])
  const schedules = ref<Schedule[]>([])

  function put(schedule: Schedule): Schedule {
    const index = schedules.value.findIndex((known) => known.id === schedule.id)
    if (index === -1) schedules.value.push(schedule)
    else schedules.value[index] = schedule
    return schedule
  }

  // --- Profiles: few, listed whole, sorted by the server.

  async function fetchProfiles(): Promise<Profile[]> {
    profiles.value = await api.get<Profile[]>('/scan-profiles')
    return profiles.value
  }

  async function createProfile(body: ProfileIn): Promise<Profile> {
    const created = await api.post<Profile>('/scan-profiles', body)
    await fetchProfiles()
    return created
  }

  async function updateProfile(id: number, body: ProfileIn): Promise<Profile> {
    const updated = await api.put<Profile>(`/scan-profiles/${id}`, body)
    await fetchProfiles()
    return updated
  }

  async function deleteProfile(id: number): Promise<void> {
    await api.delete(`/scan-profiles/${id}`)
    profiles.value = profiles.value.filter((profile) => profile.id !== id)
  }

  // --- Schedules of one project.

  async function fetchSchedules(projectId: number): Promise<void> {
    const page = await api.get<Schemas['Page_ScheduleOut_']>(`/projects/${projectId}/schedules`, {
      limit: 200,
    })
    schedules.value = page.items
  }

  async function createSchedule(projectId: number, body: ScheduleIn): Promise<Schedule> {
    return put(await api.post<Schedule>(`/projects/${projectId}/schedules`, body))
  }

  async function updateSchedule(id: number, body: ScheduleIn): Promise<Schedule> {
    return put(await api.put<Schedule>(`/schedules/${id}`, body))
  }

  async function setEnabled(id: number, enabled: boolean): Promise<Schedule> {
    return put(await api.post<Schedule>(`/schedules/${id}/${enabled ? 'enable' : 'disable'}`))
  }

  async function deleteSchedule(id: number): Promise<void> {
    await api.delete(`/schedules/${id}`)
    schedules.value = schedules.value.filter((schedule) => schedule.id !== id)
  }

  async function runs(id: number): Promise<ScheduleRun[]> {
    const page = await api.get<Schemas['Page_ScheduleRunOut_']>(`/schedules/${id}/runs`, {
      limit: 50,
    })
    return page.items
  }

  return {
    profiles,
    schedules,
    fetchProfiles,
    createProfile,
    updateProfile,
    deleteProfile,
    fetchSchedules,
    createSchedule,
    updateSchedule,
    setEnabled,
    deleteSchedule,
    runs,
  }
})
