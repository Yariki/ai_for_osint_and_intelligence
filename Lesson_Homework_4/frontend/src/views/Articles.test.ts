import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { aliases, mdi } from 'vuetify/iconsets/mdi-svg'
import Articles from './Articles.vue'
import { api } from '../api'
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  api: vi.fn(),
}))

describe('article filters', () => {
  beforeEach(() => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === '/sources' || path === '/tags') return []
      return { items: [], total: 0, page: 1, page_size: 20 }
    })
  })
  it('applies title search and resets pagination to page one', async () => {
    const wrapper = mount(Articles, {
      global: {
        plugins: [
          createVuetify({
            components,
            directives,
            icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
          }),
        ],
      },
    })
    await flushPromises()
    const search = wrapper
      .findAll('input')
      .find(
        (input) =>
          input.attributes('placeholder') === undefined && input.attributes('type') === 'text',
      )
    expect(search).toBeDefined()
    await search!.setValue('McLaren & Toyota')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const path = vi.mocked(api).mock.calls.at(-1)![0]
    const params = new URLSearchParams(path.split('?')[1])
    expect(params.get('search')).toBe('McLaren & Toyota')
    expect(params.get('page')).toBe('1')
    wrapper.unmount()
  })
})
