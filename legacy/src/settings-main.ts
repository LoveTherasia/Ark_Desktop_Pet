import { createApp } from 'vue'
import './style.css'
import SettingsApp from './SettingsApp.vue'

// 设置窗口的渲染入口：与桌宠（src/main.ts）是两个独立的 BrowserWindow
createApp(SettingsApp).mount('#app')
