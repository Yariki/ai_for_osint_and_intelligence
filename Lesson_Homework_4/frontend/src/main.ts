import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import { VApp } from 'vuetify/components/VApp'
import { VAppBar, VAppBarNavIcon } from 'vuetify/components/VAppBar'
import { VAlert } from 'vuetify/components/VAlert'
import { VBtn } from 'vuetify/components/VBtn'
import { VCard } from 'vuetify/components/VCard'
import { VChip } from 'vuetify/components/VChip'
import { VDialog } from 'vuetify/components/VDialog'
import { VRow, VCol, VSpacer } from 'vuetify/components/VGrid'
import { VIcon } from 'vuetify/components/VIcon'
import { VList, VListItem } from 'vuetify/components/VList'
import { VMain } from 'vuetify/components/VMain'
import { VNavigationDrawer } from 'vuetify/components/VNavigationDrawer'
import { VPagination } from 'vuetify/components/VPagination'
import { VProgressLinear } from 'vuetify/components/VProgressLinear'
import { VSelect } from 'vuetify/components/VSelect'
import { VSwitch } from 'vuetify/components/VSwitch'
import { VTable } from 'vuetify/components/VTable'
import { VTextField } from 'vuetify/components/VTextField'
import * as directives from 'vuetify/directives'
import { aliases, mdi } from 'vuetify/iconsets/mdi-svg'
import 'vuetify/styles'
import './style.css'
import App from './App.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/Dashboard.vue'), meta: { title: 'Overview' } },
    {
      path: '/sources',
      component: () => import('./views/Sources.vue'),
      meta: { title: 'Sources' },
    },
    {
      path: '/articles',
      component: () => import('./views/Articles.vue'),
      meta: { title: 'Articles' },
    },
    { path: '/jobs', component: () => import('./views/Jobs.vue'), meta: { title: 'Crawl jobs' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
const vuetify = createVuetify({
  components: {
    VApp,
    VAppBar,
    VAppBarNavIcon,
    VAlert,
    VBtn,
    VCard,
    VChip,
    VDialog,
    VRow,
    VCol,
    VSpacer,
    VIcon,
    VList,
    VListItem,
    VMain,
    VNavigationDrawer,
    VPagination,
    VProgressLinear,
    VSelect,
    VSwitch,
    VTable,
    VTextField,
  },
  directives,
  icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
  theme: {
    defaultTheme: 'pitwall',
    themes: {
      pitwall: {
        dark: false,
        colors: {
          primary: '#c94b27',
          secondary: '#283749',
          background: '#f4f4f0',
          surface: '#ffffff',
          success: '#28745b',
          error: '#b83f3f',
          warning: '#946418',
        },
      },
    },
  },
  defaults: {
    VCard: { elevation: 0, rounded: 'lg', border: true },
    VBtn: { elevation: 0, rounded: 'lg' },
    VTextField: { variant: 'outlined', density: 'comfortable', color: 'primary' },
    VSelect: { variant: 'outlined', density: 'comfortable', color: 'primary' },
    VAlert: { rounded: 'lg', variant: 'tonal' },
  },
})
createApp(App).use(router).use(vuetify).mount('#app')
