import { createPinia } from 'pinia'
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router, { installUnauthorizedRedirect, installWorkspaceAccessRecovery } from './router'
import './styles/base.css'

const pinia = createPinia()
const app = createApp(App)
app.use(pinia).use(router).use(ElementPlus)
installUnauthorizedRedirect(router)
installWorkspaceAccessRecovery(router)
app.mount('#app')
