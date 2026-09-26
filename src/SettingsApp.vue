<script setup lang="ts">
import SettingsPanel from './components/SettingsPanel.vue'

// 设置窗口是独立的 BrowserWindow：标题栏用系统原生拖动（-webkit-app-region: drag），
// 因此拖动它只移动它自己，不会带动桌宠，也不会触发手动 setPosition 带来的尺寸抖动。
function closeWindow() {
  window.close()
}
</script>

<template>
  <SettingsPanel @close="closeWindow" />
</template>
