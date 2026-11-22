import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, ApiError, type Schemas } from '../api/client'

type SessionInfo = Schemas['SessionInfo']
type SetupStatus = Schemas['SetupStatus']

export const useSessionStore = defineStore('session', () => {
  /** False until the server has been asked once who we are. */
  const loaded = ref(false)
  /** True while no account exists: only the setup page is usable. */
  const setupRequired = ref(false)
  const username = ref<string | null>(null)

  const isAuthenticated = computed(() => username.value !== null)

  /** Ask the server for the state of this browser's session. */
  async function load(): Promise<void> {
    const status = await api.get<SetupStatus>('/setup')
    setupRequired.value = status.setup_required
    username.value = null
    if (!status.setup_required) {
      try {
        username.value = (await api.get<SessionInfo>('/session')).username
      } catch (error) {
        // "Not authenticated" is an answer, not a failure.
        if (!(error instanceof ApiError && error.status === 401)) throw error
      }
    }
    loaded.value = true
  }

  async function setup(token: string, name: string, password: string): Promise<void> {
    await api.post<SetupStatus>('/setup', { token, username: name, password })
    setupRequired.value = false
  }

  async function login(name: string, password: string): Promise<void> {
    const session = await api.post<SessionInfo>('/login', { username: name, password })
    username.value = session.username
  }

  async function logout(): Promise<void> {
    await api.post('/logout')
    username.value = null
  }

  async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
    await api.post('/password', { current_password: currentPassword, new_password: newPassword })
  }

  /** The server no longer knows this session: it expired or was ended elsewhere. */
  function expire(): void {
    username.value = null
  }

  return {
    loaded,
    setupRequired,
    username,
    isAuthenticated,
    load,
    setup,
    login,
    logout,
    changePassword,
    expire,
  }
})
