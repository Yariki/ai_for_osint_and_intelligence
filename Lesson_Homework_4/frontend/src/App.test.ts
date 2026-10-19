import { mount, flushPromises } from '@vue/test-utils'
import { expect, it } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { aliases, mdi } from 'vuetify/iconsets/mdi-svg'
import App from './App.vue'

it('keeps desktop navigation open on initial render', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<p>Overview</p>' } }],
  })
  await router.push('/')
  const wrapper = mount(App, {
    global: {
      plugins: [
        router,
        createVuetify({
          components,
          directives,
          display: { mobileBreakpoint: 960 },
          icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
        }),
      ],
    },
  })
  await flushPromises()
  expect(wrapper.get('.v-navigation-drawer').classes()).toContain('v-navigation-drawer--active')
  wrapper.unmount()
})
