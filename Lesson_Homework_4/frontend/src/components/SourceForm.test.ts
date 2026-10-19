import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { aliases, mdi } from 'vuetify/iconsets/mdi-svg'
import SourceForm from './SourceForm.vue'

function form() {
  return mount(SourceForm, {
    props: { defaultPeriod: 60, busy: false },
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
}
describe('source creation', () => {
  it('keeps invalid forms open without emitting a source', async () => {
    const wrapper = form()
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('save')).toBeUndefined()
    expect(wrapper.get('[data-test="form-error"]').text()).toContain('name')
    await wrapper.get('[data-test="source-name"] input').setValue('Feed')
    await wrapper.get('[data-test="source-url"] input').setValue('javascript:alert(1)')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('save')).toBeUndefined()
    expect(wrapper.get('[data-test="form-error"]').text()).toContain('HTTP')
  })
  it('emits validated values with a numeric period', async () => {
    const wrapper = form()
    await wrapper.get('[data-test="source-name"] input').setValue(' Racing ')
    await wrapper.get('[data-test="source-url"] input').setValue('https://example.com/feed')
    await wrapper.get('[data-test="source-period"] input').setValue('15')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('save')?.[0]).toEqual([
      { name: 'Racing', url: 'https://example.com/feed', period_minutes: 15, enabled: true },
    ])
  })
})
